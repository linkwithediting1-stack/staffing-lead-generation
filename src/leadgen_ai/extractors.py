from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .models import ExtractedPage, Lead
from .policy import canonicalize_url, normalize_domain


EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SPACE_RE = re.compile(r"\s+")
GENERIC_EMAIL_PREFIXES = {
    "business",
    "careers",
    "career",
    "contact",
    "contactus",
    "hello",
    "hr",
    "hiring",
    "info",
    "jobs",
    "people",
    "recruitment",
    "recruiting",
    "sales",
    "talent",
    "team",
    "work",
}
SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}
CAREER_LINK_TERMS = (
    "career",
    "jobs",
    "join-us",
    "join_us",
    "join our team",
    "open-position",
    "vacanc",
    "work-with-us",
)


class _PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.json_ld_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_json_ld = False
        self._active_anchor: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        lowered = tag.lower()
        if lowered in {"style", "noscript", "template"}:
            self._skip_depth += 1
        elif lowered == "script":
            if attributes.get("type", "").lower() == "application/ld+json":
                self._in_json_ld = True
            else:
                self._skip_depth += 1
        elif lowered == "title":
            self._in_title = True
        elif lowered == "meta":
            name = (attributes.get("name") or attributes.get("property")).lower()
            content = attributes.get("content", "").strip()
            if name and content:
                self.meta[name] = content
        elif lowered == "a":
            href = attributes.get("href", "").strip()
            if href:
                self.links.append((href, ""))
                self._active_anchor = len(self.links) - 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"style", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1
        elif lowered == "script":
            if self._in_json_ld:
                self._in_json_ld = False
            elif self._skip_depth:
                self._skip_depth -= 1
        elif lowered == "title":
            self._in_title = False
        elif lowered == "a":
            self._active_anchor = None

    def handle_data(self, data: str) -> None:
        value = SPACE_RE.sub(" ", data).strip()
        if not value:
            return
        if self._in_json_ld:
            self.json_ld_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(value)
        self.text_parts.append(value)
        if self._active_anchor is not None:
            href, current = self.links[self._active_anchor]
            combined = f"{current} {value}".strip()
            self.links[self._active_anchor] = (href, combined[:200])


def extract_page(
    raw_html: str,
    url: str,
    *,
    locations: list[str],
    industries: list[str],
    hiring_terms: list[str],
) -> ExtractedPage:
    parser = _PageParser(url)
    parser.feed(raw_html)
    title = _clean_text(" ".join(parser.title_parts))
    full_text = _clean_text(" ".join(parser.text_parts))
    lower_haystack = f"{url} {title} {full_text}".lower()
    domain = normalize_domain(urlsplit(url).hostname or "")

    internal_links: list[str] = []
    social_urls: list[str] = []
    job_urls: list[str] = []
    mailto_addresses: list[str] = []
    phones: list[str] = []
    for href, anchor in parser.links:
        if href.lower().startswith("mailto:"):
            mailto_addresses.append(href[7:].split("?", 1)[0])
            continue
        if href.lower().startswith("tel:"):
            phones.append(re.sub(r"[^0-9+]", "", href[4:]))
            continue
        absolute = canonicalize_url(urljoin(url, html.unescape(href)))
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        link_domain = normalize_domain(parts.hostname)
        if any(link_domain == item or link_domain.endswith(f".{item}") for item in SOCIAL_DOMAINS):
            social_urls.append(absolute)
        combined = f"{absolute} {anchor}".lower()
        if any(term in combined for term in CAREER_LINK_TERMS):
            job_urls.append(absolute)
        if link_domain == domain:
            internal_links.append(absolute)

    found_emails = set(EMAIL_RE.findall(full_text)) | set(mailto_addresses)
    business_emails = sorted(
        {
            email.lower().strip(".,;:()[]{}<>")
            for email in found_emails
            if _is_business_email(email, domain)
        }
    )
    signals = [term for term in hiring_terms if term.lower() in lower_haystack]
    found_locations = [item for item in locations if item.lower() in lower_haystack]
    found_industries = [item for item in industries if item.lower() in lower_haystack]
    description = _clean_text(
        parser.meta.get("description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
        or ""
    )
    company_name = _company_name(parser, title, domain)
    evidence = _evidence_excerpt(full_text, [*signals, *found_locations, *found_industries])
    digest = hashlib.sha256(full_text.encode("utf-8", errors="ignore")).hexdigest()

    return ExtractedPage(
        url=url,
        final_url=url,
        domain=domain,
        title=title,
        company_name=company_name,
        description=description,
        text_excerpt=evidence,
        location=", ".join(dict.fromkeys(found_locations)),
        industry=", ".join(dict.fromkeys(found_industries)),
        emails=business_emails[:20],
        phones=list(dict.fromkeys(filter(None, phones)))[:10],
        social_urls=list(dict.fromkeys(social_urls))[:20],
        job_urls=list(dict.fromkeys(job_urls))[:50],
        internal_links=list(dict.fromkeys(internal_links))[:500],
        hiring_signals=list(dict.fromkeys(signals)),
        content_hash=digest,
    )


def pages_to_lead(pages: list[ExtractedPage], source_url: str, source_kind: str) -> Lead:
    if not pages:
        raise ValueError("At least one extracted page is required")
    home = pages[0]
    best_name = next((page.company_name for page in pages if page.company_name), home.domain)
    page_hash = hashlib.sha256(
        "".join(sorted(page.content_hash for page in pages)).encode("ascii")
    ).hexdigest()
    return Lead(
        domain=home.domain,
        company_name=best_name,
        website_url=f"{urlsplit(home.final_url).scheme}://{urlsplit(home.final_url).netloc}/",
        source_url=source_url,
        source_kind=source_kind,
        title=next((page.title for page in pages if page.title), ""),
        description=next((page.description for page in pages if page.description), ""),
        location=_join_unique(page.location for page in pages),
        industry=_join_unique(page.industry for page in pages),
        emails=_flatten_unique(page.emails for page in pages),
        phones=_flatten_unique(page.phones for page in pages),
        social_urls=_flatten_unique(page.social_urls for page in pages),
        job_urls=_flatten_unique(page.job_urls for page in pages),
        hiring_signals=_flatten_unique(page.hiring_signals for page in pages),
        evidence_urls=[page.final_url for page in pages if page.hiring_signals or page.job_urls],
        evidence_text=" ".join(page.text_excerpt for page in pages if page.text_excerpt)[:6000],
        page_hash=page_hash,
    )


def _is_business_email(email: str, website_domain: str) -> bool:
    cleaned = email.lower().strip()
    if "@" not in cleaned:
        return False
    local, email_domain = cleaned.rsplit("@", 1)
    same_domain = email_domain == website_domain or email_domain.endswith(f".{website_domain}")
    prefix = re.split(r"[._+-]", local, maxsplit=1)[0]
    return same_domain and prefix in GENERIC_EMAIL_PREFIXES


def _company_name(parser: _PageParser, title: str, domain: str) -> str:
    for raw_json in parser.json_ld_parts:
        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        name = _find_organization_name(data)
        if name:
            return _clean_text(name)[:160]
    site_name = parser.meta.get("og:site_name", "").strip()
    if site_name:
        return _clean_text(site_name)[:160]
    title_parts = [part.strip() for part in re.split(r"[|\-–—]", title) if part.strip()]
    generic = {"home", "careers", "jobs", "contact", "about us", "welcome"}
    candidates = [item for item in reversed(title_parts) if item.lower() not in generic and len(item) <= 80]
    if candidates:
        return candidates[0]
    stem = domain.split(".", 1)[0].replace("-", " ")
    return stem.title()


def _find_organization_name(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            name = _find_organization_name(item)
            if name:
                return name
    elif isinstance(value, dict):
        type_value = value.get("@type")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(item in {"Organization", "Corporation", "LocalBusiness"} for item in types):
            name = value.get("name")
            if isinstance(name, str):
                return name
        for item in value.values():
            name = _find_organization_name(item)
            if name:
                return name
    return ""


def _evidence_excerpt(text: str, needles: list[str], window: int = 220) -> str:
    lowered = text.lower()
    snippets: list[str] = []
    for needle in needles:
        position = lowered.find(needle.lower())
        if position < 0:
            continue
        start = max(0, position - window)
        end = min(len(text), position + len(needle) + window)
        snippets.append(text[start:end])
    if not snippets:
        return text[:600]
    return " ... ".join(dict.fromkeys(snippets))[:1600]


def _clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(value)).strip()


def _flatten_unique(groups) -> list[str]:
    return list(dict.fromkeys(item for group in groups for item in group if item))


def _join_unique(values) -> str:
    parts: list[str] = []
    for value in values:
        for item in value.split(","):
            cleaned = item.strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
    return ", ".join(parts)
