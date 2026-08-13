from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(slots=True)
class SearchSettings:
    provider: str = "none"
    endpoint: str = "http://127.0.0.1:8080"
    api_key_env: str = "SEARXNG_API_KEY"
    queries: list[str] = field(default_factory=list)
    results_per_query: int = 15


@dataclass(slots=True)
class CrawlerSettings:
    max_domains: int = 50
    max_pages_per_domain: int = 8
    delay_seconds: float = 1.5
    timeout_seconds: int = 20
    max_response_bytes: int = 2_000_000
    respect_robots: bool = True
    user_agent: str = "LeadGenAI/0.1 (+public-company-research)"
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    use_browser: bool = False
    browser_profile_directory: str = "data/browser-profile"
    browser_scroll_steps: int = 4


@dataclass(slots=True)
class TargetSettings:
    locations: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    hiring_terms: list[str] = field(default_factory=list)
    minimum_score: int = 45
    ai_review_threshold: int = 35


@dataclass(slots=True)
class AISettings:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key_env: str = "AI_API_KEY"
    model: str = ""
    timeout_seconds: int = 45
    max_input_characters: int = 3500
    max_output_tokens: int = 220
    max_reviews_per_run: int = 20


@dataclass(slots=True)
class OutreachSettings:
    company_name: str = "99 Staffing Services"
    sender_name: str = "Your Name"
    service_summary: str = "help growing companies hire verified candidates"
    booking_url: str = ""
    minimum_score: int = 60
    daily_send_limit: int = 15
    require_approval: bool = True
    opt_out_line: str = "If this is not relevant, reply no and I will not follow up."


@dataclass(slots=True)
class SMTPSettings:
    host: str = ""
    port: int = 587
    username_env: str = "SMTP_USERNAME"
    password_env: str = "SMTP_PASSWORD"
    from_email_env: str = "SMTP_FROM_EMAIL"
    starttls: bool = True
    ssl: bool = False


@dataclass(slots=True)
class SheetsSettings:
    enabled: bool = False
    spreadsheet_url: str = ""
    worksheet: str = "Leads"
    credentials_file_env: str = "GOOGLE_SERVICE_ACCOUNT_FILE"


@dataclass(slots=True)
class AppConfig:
    database_path: str = "data/leadgen.db"
    output_directory: str = "outputs"
    seed_file: str = "data/seeds.csv"
    search: SearchSettings = field(default_factory=SearchSettings)
    crawler: CrawlerSettings = field(default_factory=CrawlerSettings)
    target: TargetSettings = field(default_factory=TargetSettings)
    ai: AISettings = field(default_factory=AISettings)
    outreach: OutreachSettings = field(default_factory=OutreachSettings)
    smtp: SMTPSettings = field(default_factory=SMTPSettings)
    google_sheets: SheetsSettings = field(default_factory=SheetsSettings)
    config_directory: Path = field(default_factory=Path.cwd, repr=False)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).expanduser().resolve()
        load_dotenv(config_path.parent / ".env")
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        raw = _expand_env(raw)
        result = cls(
            database_path=raw.get("database_path", "data/leadgen.db"),
            output_directory=raw.get("output_directory", "outputs"),
            seed_file=raw.get("seed_file", "data/seeds.csv"),
            search=SearchSettings(**raw.get("search", {})),
            crawler=CrawlerSettings(**raw.get("crawler", {})),
            target=TargetSettings(**raw.get("target", {})),
            ai=AISettings(**raw.get("ai", {})),
            outreach=OutreachSettings(**raw.get("outreach", {})),
            smtp=SMTPSettings(**raw.get("smtp", {})),
            google_sheets=SheetsSettings(**raw.get("google_sheets", {})),
            config_directory=config_path.parent,
        )
        result.validate()
        return result

    def resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.config_directory / path).resolve()

    def validate(self) -> None:
        if self.crawler.max_domains < 1 or self.crawler.max_pages_per_domain < 1:
            raise ValueError("Crawler limits must be positive")
        if self.crawler.delay_seconds < 0:
            raise ValueError("crawler.delay_seconds cannot be negative")
        if not 0 <= self.target.minimum_score <= 100:
            raise ValueError("target.minimum_score must be between 0 and 100")
        if not 0 <= self.outreach.minimum_score <= 100:
            raise ValueError("outreach.minimum_score must be between 0 and 100")
        if self.outreach.daily_send_limit < 1:
            raise ValueError("outreach.daily_send_limit must be positive")
        if self.ai.enabled and not self.ai.model:
            raise ValueError("ai.model is required when AI is enabled")
        if self.ai.max_reviews_per_run < 0:
            raise ValueError("ai.max_reviews_per_run cannot be negative")
        if self.ai.enabled:
            _validate_api_endpoint(self.ai.base_url, "ai.base_url")
        if self.search.provider.lower() == "searxng":
            _validate_api_endpoint(self.search.endpoint, "search.endpoint")
        if self.smtp.host and not (self.smtp.starttls or self.smtp.ssl):
            if self.smtp.host.lower() not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("SMTP credentials require starttls or ssl for a non-local server")


_ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    return value


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _validate_api_endpoint(value: str, field_name: str) -> None:
    parts = urlsplit(value)
    if parts.username or parts.password:
        raise ValueError(f"{field_name} must not contain credentials")
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and (parts.hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return
    raise ValueError(f"{field_name} must use HTTPS, or HTTP on localhost")
