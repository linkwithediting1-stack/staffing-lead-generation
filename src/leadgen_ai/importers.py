from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urlsplit

from .database import Database
from .models import Lead
from .policy import normalize_domain


CONSUMER_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
    "yahoo.co.in",
}


def import_contact_history(database: Database, path: str | Path) -> dict[str, int]:
    source = Path(path)
    counters = {"rows": 0, "leads": 0, "do_not_contact": 0, "skipped": 0}
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("History CSV has no header row")
        for raw in reader:
            counters["rows"] += 1
            row = {_normalise_header(key): (value or "").strip() for key, value in raw.items() if key}
            status = _infer_status(row)
            email = _first(row, "email", "verified_email", "business_email", "recipient")
            handle_value = _first(row, "instagram_id", "instagram_handle", "profile", "username")
            website = _first(row, "website", "website_url", "company_website", "url", "domain")
            domain = _extract_domain(website, email)
            company_name = _first(row, "company", "company_name", "name", "account") or domain

            if status in {"contacted", "replied", "unreachable", "rejected"}:
                for contact_key in (email, handle_value, domain):
                    if contact_key:
                        database.add_do_not_contact(contact_key, f"Imported history: {status}")
                        counters["do_not_contact"] += 1

            if domain and domain not in CONSUMER_EMAIL_DOMAINS:
                website_url = website if website.startswith(("http://", "https://")) else f"https://{domain}/"
                lead = Lead(
                    domain=domain,
                    company_name=company_name or domain,
                    website_url=website_url,
                    source_url=str(source.resolve()),
                    source_kind="imported_history",
                    emails=[email.lower()] if email else [],
                    status=status,
                )
                stored = database.upsert_lead(lead)
                if status != stored.status:
                    database.set_lead_status(stored.id or 0, status)
                counters["leads"] += 1
            elif not any((email, handle_value, domain)):
                counters["skipped"] += 1
    return counters


def _infer_status(row: dict[str, str]) -> str:
    if _truthy(_first(row, "reply_received", "replied", "response_received")):
        return "replied"
    if _truthy(_first(row, "unreachable", "unable_to_send", "cannot_contact")):
        return "unreachable"
    if _truthy(_first(row, "message_sent", "sent", "contacted", "outreach_sent")):
        return "contacted"
    raw = _first(row, "status", "pipeline_status", "contact_status").lower()
    if any(term in raw for term in ("reply", "respond")):
        return "replied"
    if any(term in raw for term in ("unreachable", "unable", "cannot", "failed")):
        return "unreachable"
    if any(term in raw for term in ("contacted", "sent", "messaged")) and "not sent" not in raw:
        return "contacted"
    if any(term in raw for term in ("reject", "do not contact", "opt out", "unsubscribe")):
        return "rejected"
    return "new"


def _extract_domain(website: str, email: str) -> str:
    if website:
        candidate = website if "://" in website else f"https://{website}"
        hostname = urlsplit(candidate).hostname
        if hostname:
            return normalize_domain(hostname)
    if "@" in email:
        return normalize_domain(email.rsplit("@", 1)[1])
    return ""


def _normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _first(row: dict[str, str], *keys: str) -> str:
    return next((row.get(key, "") for key in keys if row.get(key, "")), "")


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "sent", "received", "done"}

