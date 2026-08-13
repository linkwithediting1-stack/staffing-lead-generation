from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class ExtractedPage:
    url: str
    final_url: str
    domain: str
    title: str = ""
    company_name: str = ""
    description: str = ""
    text_excerpt: str = ""
    location: str = ""
    industry: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social_urls: list[str] = field(default_factory=list)
    job_urls: list[str] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    hiring_signals: list[str] = field(default_factory=list)
    content_hash: str = ""


@dataclass(slots=True)
class Lead:
    domain: str
    company_name: str
    website_url: str
    source_url: str
    source_kind: str = "website"
    id: int | None = None
    title: str = ""
    description: str = ""
    location: str = ""
    industry: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social_urls: list[str] = field(default_factory=list)
    job_urls: list[str] = field(default_factory=list)
    hiring_signals: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)
    evidence_text: str = ""
    score: int = 0
    score_reason: str = ""
    confidence: float = 0.0
    status: str = "new"
    page_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def compact_evidence(self, max_chars: int = 3500) -> str:
        fields = [
            f"Company: {self.company_name}",
            f"Domain: {self.domain}",
            f"Location evidence: {self.location or 'none'}",
            f"Industry evidence: {self.industry or 'none'}",
            f"Hiring signals: {', '.join(self.hiring_signals) or 'none'}",
            f"Job URLs: {', '.join(self.job_urls[:5]) or 'none'}",
            f"Business emails: {', '.join(self.emails[:5]) or 'none'}",
            f"Description: {self.description}",
            f"Page evidence: {self.evidence_text}",
        ]
        return "\n".join(fields)[:max_chars]


@dataclass(slots=True)
class Message:
    lead_id: int
    channel: str
    recipient: str
    subject: str
    body: str
    id: int | None = None
    status: str = "draft"
    created_at: str = field(default_factory=utc_now)
    approved_at: str = ""
    sent_at: str = ""
    error: str = ""


@dataclass(slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    etag: str = ""
    last_modified: str = ""


@dataclass(slots=True)
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = "search"


JsonDict = dict[str, Any]

