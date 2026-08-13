from __future__ import annotations

import csv
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from .config import SearchSettings
from .models import SearchResult


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_seed_results(path: str | Path) -> list[SearchResult]:
    seed_path = Path(path)
    if not seed_path.exists():
        return []
    results: list[SearchResult] = []
    with seed_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=(row.get("title") or "").strip(),
                    snippet=(row.get("snippet") or "").strip(),
                    source=(row.get("source") or "seed").strip(),
                )
            )
    return results


def discover_search_results(settings: SearchSettings) -> list[SearchResult]:
    if settings.provider.lower() in {"", "none", "disabled"}:
        return []
    if settings.provider.lower() != "searxng":
        raise ValueError(f"Unsupported search provider: {settings.provider}")
    return _search_searxng(settings)


def _search_searxng(settings: SearchSettings) -> list[SearchResult]:
    endpoint = settings.endpoint.rstrip("/") + "/search"
    api_key = os.environ.get(settings.api_key_env, "") if settings.api_key_env else ""
    results: list[SearchResult] = []
    seen: set[str] = set()
    for query in settings.queries:
        parameters = urllib.parse.urlencode({"q": query, "format": "json", "language": "en"})
        headers = {
            "Accept": "application/json",
            "User-Agent": "LeadGenAI/0.1",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(f"{endpoint}?{parameters}", headers=headers)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=30) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("SearXNG response exceeded 2 MB")
        payload = json.loads(body)
        for item in payload.get("results", [])[: settings.results_per_query]:
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("content") or ""),
                    source=f"searxng:{query}",
                )
            )
    return results
