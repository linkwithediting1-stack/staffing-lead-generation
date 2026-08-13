from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .config import AppConfig
from .extractors import extract_page, pages_to_lead
from .models import ExtractedPage, Lead, SearchResult
from .policy import UrlPolicy, canonicalize_url, normalize_domain


PRIORITY_TERMS = {
    "careers": 100,
    "career": 100,
    "jobs": 95,
    "vacancies": 95,
    "join": 90,
    "contact": 70,
    "about": 60,
    "team": 55,
}


@dataclass(slots=True)
class CrawlReport:
    leads: list[Lead] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_fetched: int = 0


class CompanyCrawler:
    def __init__(self, config: AppConfig, fetcher):
        self.config = config
        self.fetcher = fetcher
        self.policy = UrlPolicy(
            allowed_domains=config.crawler.allowed_domains,
            denied_domains=config.crawler.denied_domains,
        )

    def crawl(self, results: list[SearchResult]) -> CrawlReport:
        report = CrawlReport()
        grouped = self._group_by_domain(results)
        for domain, candidates in list(grouped.items())[: self.config.crawler.max_domains]:
            pages, errors = self._crawl_domain(domain, candidates)
            report.pages_fetched += len(pages)
            report.errors.extend(errors)
            if not pages:
                continue
            try:
                lead = pages_to_lead(pages, candidates[0].url, candidates[0].source)
            except ValueError as exc:
                report.errors.append(f"{domain}: {exc}")
                continue
            report.leads.append(lead)
        return report

    def _group_by_domain(self, results: list[SearchResult]) -> dict[str, list[SearchResult]]:
        grouped: dict[str, list[SearchResult]] = {}
        for result in results:
            try:
                url = self.policy.validate(result.url)
            except ValueError:
                continue
            domain = normalize_domain(urlsplit(url).hostname or "")
            if not domain:
                continue
            normalized = SearchResult(url=url, title=result.title, snippet=result.snippet, source=result.source)
            grouped.setdefault(domain, []).append(normalized)
        return grouped

    def _crawl_domain(
        self,
        domain: str,
        candidates: list[SearchResult],
    ) -> tuple[list[ExtractedPage], list[str]]:
        queue: list[tuple[int, int, str]] = []
        queued: set[str] = set()
        visited: set[str] = set()
        pages: list[ExtractedPage] = []
        errors: list[str] = []
        sequence = 0

        for result in candidates:
            sequence += 1
            self._push(queue, queued, result.url, sequence)
            parts = urlsplit(result.url)
            home = f"{parts.scheme}://{parts.netloc}/"
            sequence += 1
            self._push(queue, queued, home, sequence)

        while queue and len(pages) < self.config.crawler.max_pages_per_domain:
            _, _, url = heapq.heappop(queue)
            if url in visited:
                continue
            visited.add(url)
            try:
                result = self.fetcher.fetch(url)
                if normalize_domain(urlsplit(result.final_url).hostname or "") != domain:
                    raise ValueError(f"Redirected outside company domain to {result.final_url}")
                page = extract_page(
                    result.text,
                    result.final_url,
                    locations=self.config.target.locations,
                    industries=self.config.target.industries,
                    hiring_terms=self.config.target.hiring_terms,
                )
                page.final_url = result.final_url
                pages.append(page)
                for link in page.internal_links:
                    if normalize_domain(urlsplit(link).hostname or "") != domain:
                        continue
                    sequence += 1
                    self._push(queue, queued, link, sequence)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        return pages, errors

    @staticmethod
    def _push(queue: list[tuple[int, int, str]], queued: set[str], url: str, sequence: int) -> None:
        canonical = canonicalize_url(url)
        if canonical in queued:
            return
        queued.add(canonical)
        lowered = canonical.lower()
        priority = max((value for term, value in PRIORITY_TERMS.items() if term in lowered), default=10)
        heapq.heappush(queue, (-priority, sequence, canonical))

