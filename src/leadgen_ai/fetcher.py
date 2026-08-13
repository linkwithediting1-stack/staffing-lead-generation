from __future__ import annotations

import codecs
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from .config import CrawlerSettings
from .models import FetchResult
from .policy import UrlPolicy, normalize_domain


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: UrlPolicy):
        super().__init__()
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.full_url, newurl)
        validated = self.policy.validate(absolute)
        if urlsplit(req.full_url).scheme == "https" and urlsplit(validated).scheme != "https":
            raise urllib.error.HTTPError(validated, code, "Refusing HTTPS downgrade", headers, fp)
        self.policy.assert_public_dns(validated)
        return super().redirect_request(req, fp, code, msg, headers, validated)


@dataclass(slots=True)
class PublicWebFetcher:
    settings: CrawlerSettings
    policy: UrlPolicy
    _last_request: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)

    def fetch(self, url: str, *, check_robots: bool = True) -> FetchResult:
        validated = self.policy.validate(url)
        self.policy.assert_public_dns(validated)
        if check_robots and self.settings.respect_robots and not self._can_fetch(validated):
            raise PermissionError(f"robots.txt disallows crawling: {validated}")
        self._pace(validated)
        request = urllib.request.Request(
            validated,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        opener = urllib.request.build_opener(_ValidatedRedirectHandler(self.policy))
        try:
            with opener.open(request, timeout=self.settings.timeout_seconds) as response:
                final_url = self.policy.validate(response.geturl())
                self.policy.assert_public_dns(final_url)
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise ValueError(f"Unsupported content type {content_type}: {final_url}")
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > self.settings.max_response_bytes:
                    raise ValueError(f"Response too large: {final_url}")
                body = response.read(self.settings.max_response_bytes + 1)
                if len(body) > self.settings.max_response_bytes:
                    raise ValueError(f"Response exceeded byte limit: {final_url}")
                charset = response.headers.get_content_charset() or _detect_charset(body) or "utf-8"
                try:
                    decoded = body.decode(charset, errors="replace")
                except LookupError:
                    decoded = body.decode("utf-8", errors="replace")
                return FetchResult(
                    requested_url=validated,
                    final_url=final_url,
                    status_code=getattr(response, "status", 200),
                    content_type=content_type,
                    text=decoded,
                    etag=response.headers.get("ETag", ""),
                    last_modified=response.headers.get("Last-Modified", ""),
                )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {validated}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error for {validated}: {exc.reason}") from exc

    def can_fetch(self, url: str) -> bool:
        validated = self.policy.validate(url)
        self.policy.assert_public_dns(validated)
        return self._can_fetch(validated)

    def _can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            parser.set_url(robots_url)
            try:
                result = self.fetch(robots_url, check_robots=False)
                parser.parse(result.text.splitlines())
            except Exception:
                parser.parse([])
            self._robots[origin] = parser
        return parser.can_fetch(self.settings.user_agent, url)

    def _pace(self, url: str) -> None:
        domain = normalize_domain(urlsplit(url).hostname or "")
        elapsed = time.monotonic() - self._last_request[domain]
        remaining = self.settings.delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request[domain] = time.monotonic()


def _detect_charset(body: bytes) -> str:
    if body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    head = body[:4096].lower()
    marker = b"charset="
    position = head.find(marker)
    if position < 0:
        return ""
    value = head[position + len(marker) : position + len(marker) + 40]
    value = value.split(b'"', 1)[0].split(b"'", 1)[0].split(b";", 1)[0].split(b">", 1)[0]
    try:
        return value.decode("ascii").strip()
    except UnicodeDecodeError:
        return ""
