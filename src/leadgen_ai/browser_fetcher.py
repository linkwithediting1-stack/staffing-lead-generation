from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from .config import CrawlerSettings
from .fetcher import PublicWebFetcher
from .models import FetchResult
from .policy import UrlPolicy, normalize_domain


class BrowserFetcher:
    """Optional renderer for public JavaScript-heavy company pages.

    It deliberately does not fill login forms or send messages. A persistent browser
    profile may hold a session the operator created manually for sites that permit it.
    """

    def __init__(self, settings: CrawlerSettings, policy: UrlPolicy, project_root: Path):
        self.settings = settings
        self.policy = policy
        self._subresource_policy = UrlPolicy(denied_domains=settings.denied_domains)
        profile = Path(settings.browser_profile_directory).expanduser()
        self.profile_path = profile if profile.is_absolute() else (project_root / profile).resolve()
        self._robots_checker = PublicWebFetcher(settings, policy)
        self._last_request: dict[str, float] = defaultdict(float)
        self._playwright = None
        self._context = None
        self._validated_subresource_hosts: set[str] = set()

    def fetch(self, url: str, *, check_robots: bool = True) -> FetchResult:
        validated = self.policy.validate(url)
        self.policy.assert_public_dns(validated)
        if check_robots and self.settings.respect_robots and not self._robots_checker.can_fetch(validated):
            raise PermissionError(f"robots.txt disallows browser rendering: {validated}")
        self._pace(validated)
        context = self._ensure_context()
        page = context.new_page()
        try:
            response = page.goto(
                validated,
                wait_until="domcontentloaded",
                timeout=self.settings.timeout_seconds * 1000,
            )
            for _ in range(self.settings.browser_scroll_steps):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(350)
            page.wait_for_timeout(500)
            final_url = self.policy.validate(page.url)
            self.policy.assert_public_dns(final_url)
            content = page.content()
            status = response.status if response else 200
        finally:
            page.close()
        if len(content.encode("utf-8")) > self.settings.max_response_bytes:
            raise ValueError(f"Rendered response exceeded byte limit: {final_url}")
        return FetchResult(
            requested_url=validated,
            final_url=final_url,
            status_code=status,
            content_type="text/html",
            text=content,
        )

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure_context(self):
        if self._context is not None:
            return self._context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser mode requires: pip install -e .[browser] && playwright install chromium"
            ) from exc
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_path),
            headless=True,
            viewport={"width": 1365, "height": 900},
            user_agent=self.settings.user_agent,
            service_workers="block",
        )
        self._context.route("**/*", self._route_request)
        return self._context

    def _route_request(self, route) -> None:
        request = route.request
        if request.resource_type in {"font", "image", "media"}:
            route.abort()
            return
        parts = urlsplit(request.url)
        if parts.scheme not in {"http", "https"}:
            route.continue_()
            return
        try:
            validated = self._subresource_policy.validate(request.url)
            hostname = normalize_domain(urlsplit(validated).hostname or "")
            if hostname not in self._validated_subresource_hosts:
                self._subresource_policy.assert_public_dns(validated)
                self._validated_subresource_hosts.add(hostname)
        except ValueError:
            route.abort()
            return
        route.continue_()

    def _pace(self, url: str) -> None:
        domain = normalize_domain(urlsplit(url).hostname or "")
        elapsed = time.monotonic() - self._last_request[domain]
        remaining = self.settings.delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request[domain] = time.monotonic()
