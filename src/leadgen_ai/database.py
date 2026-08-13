from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import Lead, Message, utc_now


LIST_FIELDS = (
    "emails",
    "phones",
    "social_urls",
    "job_urls",
    "hiring_signals",
    "evidence_urls",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY,
                    domain TEXT NOT NULL UNIQUE,
                    company_name TEXT NOT NULL,
                    website_url TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    emails TEXT NOT NULL DEFAULT '[]',
                    phones TEXT NOT NULL DEFAULT '[]',
                    social_urls TEXT NOT NULL DEFAULT '[]',
                    job_urls TEXT NOT NULL DEFAULT '[]',
                    hiring_signals TEXT NOT NULL DEFAULT '[]',
                    evidence_urls TEXT NOT NULL DEFAULT '[]',
                    evidence_text TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL DEFAULT 0,
                    score_reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'new',
                    page_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    approved_at TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(lead_id, channel, recipient)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

                CREATE TABLE IF NOT EXISTS do_not_contact (
                    contact_key TEXT PRIMARY KEY,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY,
                    event TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )

    def get_lead(self, lead_id: int) -> Lead | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return self._row_to_lead(row) if row else None

    def get_lead_by_domain(self, domain: str) -> Lead | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM leads WHERE domain = ?", (domain,)).fetchone()
        return self._row_to_lead(row) if row else None

    def upsert_lead(self, lead: Lead) -> Lead:
        existing = self.get_lead_by_domain(lead.domain)
        if existing:
            lead = _merge_leads(existing, lead)
        lead.updated_at = utc_now()
        values = self._lead_values(lead)
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "domain")
        with self.connect() as connection:
            connection.execute(
                f"""
                INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders})
                ON CONFLICT(domain) DO UPDATE SET {updates}
                """,
                tuple(values[column] for column in columns),
            )
            row = connection.execute("SELECT * FROM leads WHERE domain = ?", (lead.domain,)).fetchone()
        assert row is not None
        return self._row_to_lead(row)

    def list_leads(
        self,
        *,
        status: str | None = None,
        min_score: int = 0,
        limit: int = 500,
    ) -> list[Lead]:
        query = "SELECT * FROM leads WHERE score >= ?"
        parameters: list[object] = [min_score]
        if status:
            query += " AND status = ?"
            parameters.append(status)
        query += " ORDER BY score DESC, updated_at DESC LIMIT ?"
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_lead(row) for row in rows]

    def set_lead_status(self, lead_id: int, status: str) -> None:
        allowed = {"new", "review", "approved", "rejected", "contacted", "replied", "unreachable"}
        if status not in allowed:
            raise ValueError(f"Unsupported lead status: {status}")
        with self.connect() as connection:
            connection.execute(
                "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), lead_id),
            )
        self.audit("lead_status", "lead", str(lead_id), {"status": status})

    def create_message(self, message: Message) -> Message:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM messages WHERE lead_id = ? AND channel = ? AND recipient = ?",
                (message.lead_id, message.channel, message.recipient),
            ).fetchone()
            if existing and existing["status"] != "draft":
                return self._row_to_message(existing)
            connection.execute(
                """
                INSERT INTO messages
                    (lead_id, channel, recipient, subject, body, status, created_at, approved_at, sent_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lead_id, channel, recipient) DO UPDATE SET
                    subject=excluded.subject,
                    body=excluded.body,
                    error=''
                """,
                (
                    message.lead_id,
                    message.channel,
                    message.recipient,
                    message.subject,
                    message.body,
                    message.status,
                    message.created_at,
                    message.approved_at,
                    message.sent_at,
                    message.error,
                ),
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE lead_id = ? AND channel = ? AND recipient = ?",
                (message.lead_id, message.channel, message.recipient),
            ).fetchone()
        assert row is not None
        return self._row_to_message(row)

    def list_messages(self, status: str | None = None, limit: int = 500) -> list[Message]:
        query = "SELECT * FROM messages"
        parameters: list[object] = []
        if status:
            query += " WHERE status = ?"
            parameters.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_message(row) for row in rows]

    def approve_message(self, message_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET status='approved', approved_at=?, error='' WHERE id=? AND status='draft'",
                (utc_now(), message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Message was not found or is not a draft")
        self.audit("message_approved", "message", str(message_id))

    def reject_message(self, message_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET status='rejected', error='' WHERE id=? AND status IN ('draft', 'approved')",
                (message_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Message was not found or cannot be rejected")
        self.audit("message_rejected", "message", str(message_id))

    def mark_message_sent(self, message_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE messages SET status='sent', sent_at=?, error='' WHERE id=?",
                (utc_now(), message_id),
            )
            connection.execute(
                """
                UPDATE leads SET status='contacted', updated_at=?
                WHERE id=(SELECT lead_id FROM messages WHERE id=?)
                  AND status NOT IN ('replied')
                """,
                (utc_now(), message_id),
            )
        self.audit("message_sent", "message", str(message_id))

    def mark_message_error(self, message_id: int, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE messages SET status='error', error=? WHERE id=?",
                (error[:1000], message_id),
            )
        self.audit("message_error", "message", str(message_id), {"error": error[:300]})

    def add_do_not_contact(self, contact_key: str, reason: str = "") -> None:
        key = _normalize_contact_key(contact_key)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO do_not_contact(contact_key, reason, created_at) VALUES (?, ?, ?)",
                (key, reason, utc_now()),
            )
        self.audit("dnc_added", "contact", key, {"reason": reason})

    def is_do_not_contact(self, contact_key: str) -> bool:
        key = _normalize_contact_key(contact_key)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM do_not_contact WHERE contact_key IN (?, ?)",
                (key, key.split("@")[-1] if "@" in key else key),
            ).fetchone()
        return bool(row)

    def get_ai_cache(self, cache_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM ai_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def set_ai_cache(self, cache_key: str, response: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ai_cache(cache_key, response_json, created_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(response, ensure_ascii=True), utc_now()),
            )

    def sent_today(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE status='sent' AND date(sent_at)=date('now')"
            ).fetchone()
        return int(row[0])

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            lead_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM leads GROUP BY status"
            ).fetchall()
            message_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM messages GROUP BY status"
            ).fetchall()
        result = {f"leads_{row['status']}": row["count"] for row in lead_rows}
        result.update({f"messages_{row['status']}": row["count"] for row in message_rows})
        result["leads_total"] = sum(row["count"] for row in lead_rows)
        result["messages_total"] = sum(row["count"] for row in message_rows)
        return result

    def audit(
        self,
        event: str,
        entity_type: str,
        entity_id: str,
        details: dict | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(event, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (event, entity_type, entity_id, json.dumps(details or {}, ensure_ascii=True), utc_now()),
            )

    @staticmethod
    def _lead_values(lead: Lead) -> dict[str, object]:
        return {
            "domain": lead.domain,
            "company_name": lead.company_name,
            "website_url": lead.website_url,
            "source_url": lead.source_url,
            "source_kind": lead.source_kind,
            "title": lead.title,
            "description": lead.description,
            "location": lead.location,
            "industry": lead.industry,
            "emails": json.dumps(lead.emails, ensure_ascii=True),
            "phones": json.dumps(lead.phones, ensure_ascii=True),
            "social_urls": json.dumps(lead.social_urls, ensure_ascii=True),
            "job_urls": json.dumps(lead.job_urls, ensure_ascii=True),
            "hiring_signals": json.dumps(lead.hiring_signals, ensure_ascii=True),
            "evidence_urls": json.dumps(lead.evidence_urls, ensure_ascii=True),
            "evidence_text": lead.evidence_text,
            "score": lead.score,
            "score_reason": lead.score_reason,
            "confidence": lead.confidence,
            "status": lead.status,
            "page_hash": lead.page_hash,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
        }

    @staticmethod
    def _row_to_lead(row: sqlite3.Row) -> Lead:
        values = dict(row)
        for field_name in LIST_FIELDS:
            values[field_name] = json.loads(values[field_name] or "[]")
        return Lead(**values)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(**dict(row))


def _ordered_union(first: list[str], second: list[str], limit: int = 100) -> list[str]:
    return list(dict.fromkeys(item for item in [*first, *second] if item))[:limit]


def _merge_leads(existing: Lead, incoming: Lead) -> Lead:
    preserved_status = existing.status if existing.status not in {"new", "review"} else incoming.status
    for field_name in LIST_FIELDS:
        setattr(
            incoming,
            field_name,
            _ordered_union(getattr(existing, field_name), getattr(incoming, field_name)),
        )
    incoming.id = existing.id
    incoming.company_name = incoming.company_name or existing.company_name
    incoming.description = incoming.description or existing.description
    incoming.location = incoming.location or existing.location
    incoming.industry = incoming.industry or existing.industry
    incoming.status = preserved_status
    incoming.created_at = existing.created_at
    return incoming


def _normalize_contact_key(value: str) -> str:
    return value.strip().lower().removeprefix("www.").rstrip("/")

