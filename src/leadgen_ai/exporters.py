from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

from .config import AppConfig
from .database import Database
from .models import Lead


LEAD_COLUMNS = [
    "id",
    "company_name",
    "domain",
    "website_url",
    "location",
    "industry",
    "score",
    "confidence",
    "status",
    "emails",
    "phones",
    "hiring_signals",
    "job_urls",
    "evidence_urls",
    "score_reason",
    "source_kind",
    "source_url",
    "updated_at",
]


def export_csv(database: Database, path: str | Path, min_score: int = 0) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    leads = database.list_leads(min_score=min_score, limit=100_000)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEAD_COLUMNS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(_lead_row(lead))
    return output


def export_jsonl(database: Database, path: str | Path, min_score: int = 0) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for lead in database.list_leads(min_score=min_score, limit=100_000):
            handle.write(json.dumps(asdict(lead), ensure_ascii=True) + "\n")
    return output


def sync_google_sheet(config: AppConfig, database: Database) -> tuple[int, int]:
    settings = config.google_sheets
    if not settings.enabled or not settings.spreadsheet_url:
        raise ValueError("Enable google_sheets and set spreadsheet_url in config.json")
    credentials_path = os.environ.get(settings.credentials_file_env, "")
    if not credentials_path:
        raise ValueError(f"Set {settings.credentials_file_env} in .env")
    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("Google Sheets sync requires: pip install -e .[sheets]") from exc

    credentials_file = config.resolve_path(credentials_path)
    if not credentials_file.exists():
        raise ValueError(f"Google service-account file does not exist: {credentials_file}")
    client = gspread.service_account(filename=str(credentials_file))
    spreadsheet = client.open_by_url(settings.spreadsheet_url)
    try:
        worksheet = spreadsheet.worksheet(settings.worksheet)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=settings.worksheet, rows=1000, cols=len(LEAD_COLUMNS))

    existing = worksheet.get_all_values()
    if not existing:
        worksheet.update(values=[LEAD_COLUMNS], range_name="A1")
        existing = [LEAD_COLUMNS]
    if existing[0] != LEAD_COLUMNS:
        raise ValueError(
            f"Worksheet header does not match expected columns. Use an empty '{settings.worksheet}' tab."
        )
    domain_column = LEAD_COLUMNS.index("domain")
    row_by_domain = {
        row[domain_column].strip().lower(): index
        for index, row in enumerate(existing[1:], start=2)
        if len(row) > domain_column and row[domain_column].strip()
    }
    updates = 0
    appends: list[list[str]] = []
    for lead in database.list_leads(limit=100_000):
        row = [_sheet_value(_lead_row(lead)[column]) for column in LEAD_COLUMNS]
        row_number = row_by_domain.get(lead.domain.lower())
        if row_number:
            worksheet.update(values=[row], range_name=f"A{row_number}")
            updates += 1
        else:
            appends.append(row)
    if appends:
        worksheet.append_rows(appends, value_input_option="RAW")
    return updates, len(appends)


def _lead_row(lead: Lead) -> dict[str, str | int | float]:
    raw = {
        "id": lead.id or "",
        "company_name": lead.company_name,
        "domain": lead.domain,
        "website_url": lead.website_url,
        "location": lead.location,
        "industry": lead.industry,
        "score": lead.score,
        "confidence": lead.confidence,
        "status": lead.status,
        "emails": ", ".join(lead.emails),
        "phones": ", ".join(lead.phones),
        "hiring_signals": ", ".join(lead.hiring_signals),
        "job_urls": ", ".join(lead.job_urls),
        "evidence_urls": ", ".join(lead.evidence_urls),
        "score_reason": lead.score_reason,
        "source_kind": lead.source_kind,
        "source_url": lead.source_url,
        "updated_at": lead.updated_at,
    }
    return {key: _csv_safe(value) for key, value in raw.items()}


def _csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _sheet_value(value) -> str:
    return str(value) if value is not None else ""
