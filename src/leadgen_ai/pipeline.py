from __future__ import annotations

from dataclasses import dataclass, field

from .ai import LowTokenAIReviewer
from .browser_fetcher import BrowserFetcher
from .config import AppConfig
from .crawler import CompanyCrawler
from .database import Database
from .fetcher import PublicWebFetcher
from .models import SearchResult
from .policy import UrlPolicy
from .scoring import score_lead
from .search import discover_search_results, load_seed_results


@dataclass(slots=True)
class PipelineReport:
    discovered_urls: int = 0
    pages_fetched: int = 0
    leads_saved: int = 0
    qualified_leads: int = 0
    ai_reviews: int = 0
    errors: list[str] = field(default_factory=list)


def run_pipeline(config: AppConfig, *, skip_search: bool = False, skip_ai: bool = False) -> PipelineReport:
    database = Database(config.resolve_path(config.database_path))
    database.initialize()
    report = PipelineReport()
    results = load_seed_results(config.resolve_path(config.seed_file))
    if not skip_search:
        try:
            results.extend(discover_search_results(config.search))
        except Exception as exc:
            report.errors.append(f"Search discovery failed: {exc}")
    results = _deduplicate_results(results)
    report.discovered_urls = len(results)

    policy = UrlPolicy(
        allowed_domains=config.crawler.allowed_domains,
        denied_domains=config.crawler.denied_domains,
    )
    if config.crawler.use_browser:
        fetcher = BrowserFetcher(config.crawler, policy, config.config_directory)
    else:
        fetcher = PublicWebFetcher(config.crawler, policy)

    try:
        crawl_report = CompanyCrawler(config, fetcher).crawl(results)
    finally:
        close = getattr(fetcher, "close", None)
        if close:
            close()
    report.pages_fetched = crawl_report.pages_fetched
    report.errors.extend(crawl_report.errors)

    saved = []
    for lead in crawl_report.leads:
        lead = score_lead(lead, config.target.locations, config.target.industries)
        lead.status = "review" if lead.score >= config.target.minimum_score else "new"
        stored = database.upsert_lead(lead)
        saved.append(stored)
        report.leads_saved += 1

    if config.ai.enabled and not skip_ai and config.ai.max_reviews_per_run:
        reviewer = LowTokenAIReviewer(config.ai, database)
        candidates = [
            lead
            for lead in sorted(saved, key=lambda item: item.score, reverse=True)
            if lead.score >= config.target.ai_review_threshold
            and lead.status in {"new", "review"}
        ][: config.ai.max_reviews_per_run]
        for lead in candidates:
            try:
                review = reviewer.review(lead)
                lead = reviewer.apply(lead, review)
                lead.status = "review" if lead.score >= config.target.minimum_score else "new"
                database.upsert_lead(lead)
                report.ai_reviews += 1
            except Exception as exc:
                report.errors.append(f"AI review failed for {lead.domain}: {exc}")

    report.qualified_leads = len(
        database.list_leads(min_score=config.target.minimum_score, limit=100_000)
    )
    database.audit(
        "pipeline_run",
        "pipeline",
        "current",
        {
            "discovered_urls": report.discovered_urls,
            "pages_fetched": report.pages_fetched,
            "leads_saved": report.leads_saved,
            "qualified_leads": report.qualified_leads,
            "ai_reviews": report.ai_reviews,
            "errors": len(report.errors),
        },
    )
    return report


def _deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    output: list[SearchResult] = []
    seen: set[str] = set()
    for result in results:
        key = result.url.strip().rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output

