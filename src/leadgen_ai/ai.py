from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import AISettings
from .database import Database
from .models import Lead


PROMPT_VERSION = "lead-review-v1"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(slots=True)
class AIReview:
    fit_score: int
    verified_summary: str
    hiring_signal: str
    opener: str
    confidence: float


class LowTokenAIReviewer:
    def __init__(self, settings: AISettings, database: Database):
        self.settings = settings
        self.database = database

    def review(self, lead: Lead) -> AIReview:
        evidence = lead.compact_evidence(self.settings.max_input_characters)
        cache_key = hashlib.sha256(
            f"{PROMPT_VERSION}\n{self.settings.model}\n{evidence}".encode("utf-8")
        ).hexdigest()
        cached = self.database.get_ai_cache(cache_key)
        if cached:
            return _parse_review(cached)

        system = (
            "You qualify B2B staffing leads using only supplied public evidence. "
            "Never infer facts, hiring urgency, employee identity, age, gender, budget, or intent. "
            "Return compact JSON only with keys fit_score (0-100), verified_summary, "
            "hiring_signal, opener, confidence (0-1). Use an empty string when unsupported. "
            "The opener must be one factual, polite sentence under 24 words."
        )
        user = f"Evaluate this company lead for staffing outreach:\n{evidence}"
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": self.settings.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = os.environ.get(self.settings.api_key_env, "") if self.settings.api_key_env else ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=self.settings.timeout_seconds) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise RuntimeError("AI API response exceeded 1 MB")
            raw = json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"AI API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"AI API network error: {exc.reason}") from exc

        content = raw["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        review = _parse_review(parsed)
        self.database.set_ai_cache(cache_key, parsed)
        return review

    @staticmethod
    def apply(lead: Lead, review: AIReview) -> Lead:
        bounded_ai = max(0, min(100, review.fit_score))
        lead.score = round(lead.score * 0.8 + bounded_ai * 0.2)
        if review.verified_summary:
            lead.description = review.verified_summary[:500]
        details = [lead.score_reason, f"AI fit {bounded_ai}/100"]
        if review.hiring_signal:
            details.append(f"AI verified: {review.hiring_signal[:180]}")
        lead.score_reason = "; ".join(filter(None, details))
        lead.confidence = min(1.0, max(lead.confidence, review.confidence))
        return lead


def _extract_json(content: str | dict) -> dict:
    if isinstance(content, dict):
        return content
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("AI API did not return a JSON object")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("AI response must be a JSON object")
    return value


def _parse_review(value: dict) -> AIReview:
    try:
        fit_score = int(value.get("fit_score", 0))
        confidence = float(value.get("confidence", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("AI review has invalid numeric fields") from exc
    return AIReview(
        fit_score=max(0, min(100, fit_score)),
        verified_summary=str(value.get("verified_summary") or "").strip(),
        hiring_signal=str(value.get("hiring_signal") or "").strip(),
        opener=str(value.get("opener") or "").strip(),
        confidence=max(0.0, min(1.0, confidence)),
    )
