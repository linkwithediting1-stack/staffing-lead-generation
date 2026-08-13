from __future__ import annotations

from urllib.parse import urlsplit

from .config import AppConfig
from .policy import UrlPolicy, normalize_domain


def create_manual_browser_session(config: AppConfig, url: str) -> None:
    """Open an operator-controlled browser and persist its permitted-site session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Browser sessions require: pip install -e .[browser] && playwright install chromium"
        ) from exc

    policy = UrlPolicy(
        allowed_domains=config.crawler.allowed_domains,
        denied_domains=config.crawler.denied_domains,
    )
    validated = policy.validate(url)
    domain = normalize_domain(urlsplit(validated).hostname or "")
    if not config.crawler.allowed_domains:
        raise ValueError(
            "Set crawler.allowed_domains before creating a saved browser session"
        )
    profile = config.resolve_path(config.crawler.browser_profile_directory)
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            headless=False,
            viewport={"width": 1365, "height": 900},
            user_agent=config.crawler.user_agent,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(validated, wait_until="domcontentloaded")
        print(f"Browser opened for {domain}. Sign in manually if the site's terms permit automation.")
        input("Press Enter here after the session is ready...")
        context.close()

