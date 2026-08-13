from __future__ import annotations

import smtplib
import ssl
import re
from email.message import EmailMessage

from .config import AppConfig
from .database import Database
from .models import Lead, Message


def create_outreach_drafts(config: AppConfig, database: Database, limit: int = 100) -> list[Message]:
    leads = database.list_leads(min_score=config.outreach.minimum_score, limit=limit)
    drafts: list[Message] = []
    for lead in leads:
        if lead.status in {"rejected", "contacted", "replied", "unreachable"}:
            continue
        if database.is_do_not_contact(lead.domain):
            continue
        for recipient in lead.emails:
            if not _is_role_business_email(recipient, lead.domain):
                continue
            if database.is_do_not_contact(recipient):
                continue
            subject, body = render_message(config, lead)
            message = database.create_message(
                Message(
                    lead_id=lead.id or 0,
                    channel="email",
                    recipient=recipient,
                    subject=subject,
                    body=body,
                )
            )
            drafts.append(message)
            break
    return drafts


def render_message(config: AppConfig, lead: Lead) -> tuple[str, str]:
    signal = _safe_signal(lead)
    company_name = _header_text(lead.company_name)[:120] or lead.domain
    subject = f"Hiring support for {company_name}"
    lines = [
        "Hello," ,
        "",
        signal.replace(lead.company_name, company_name),
        (
            f"I am {config.outreach.sender_name} from {config.outreach.company_name}. "
            f"We {config.outreach.service_summary}."
        ),
        "",
        "Would it be useful to share a short, role-specific candidate plan for your current hiring needs?",
    ]
    if config.outreach.booking_url:
        lines.extend(["", f"You can also choose a time here: {config.outreach.booking_url}"])
    lines.extend(["", config.outreach.opt_out_line, "", f"Regards,\n{config.outreach.sender_name}"])
    return subject[:180], "\n".join(lines)


def send_approved_messages(
    config: AppConfig,
    database: Database,
    *,
    confirm_send: bool,
    limit: int | None = None,
) -> tuple[int, list[str]]:
    if not confirm_send:
        raise ValueError("Actual sending requires --confirm-send")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be a positive integer")
    smtp = config.smtp
    if not smtp.host:
        raise ValueError("smtp.host is required")
    if config.outreach.require_approval:
        candidates = database.list_messages(status="approved", limit=500)
    else:
        candidates = database.list_messages(status="draft", limit=500)

    remaining = max(0, config.outreach.daily_send_limit - database.sent_today())
    requested = remaining if limit is None else min(remaining, limit)
    candidates = candidates[:requested]
    if not candidates:
        return 0, []

    import os

    username = os.environ.get(smtp.username_env, "")
    password = os.environ.get(smtp.password_env, "")
    from_email = os.environ.get(smtp.from_email_env, "") or username
    if not from_email:
        raise ValueError(f"Set {smtp.from_email_env} or {smtp.username_env} in .env")

    sent = 0
    errors: list[str] = []
    context = ssl.create_default_context()
    if smtp.ssl:
        client_context = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=30, context=context)
    else:
        client_context = smtplib.SMTP(smtp.host, smtp.port, timeout=30)
    with client_context as client:
        if smtp.starttls and not smtp.ssl:
            client.starttls(context=context)
        if username:
            client.login(username, password)
        for message in candidates:
            lead = database.get_lead(message.lead_id)
            if (
                not lead
                or not _is_role_business_email(message.recipient, lead.domain)
                or database.is_do_not_contact(message.recipient)
                or database.is_do_not_contact(lead.domain)
            ):
                database.mark_message_error(message.id or 0, "Recipient or company is on do-not-contact list")
                continue
            email = EmailMessage()
            email["From"] = from_email
            email["To"] = message.recipient
            email["Subject"] = message.subject
            email.set_content(message.body)
            try:
                client.send_message(email)
                database.mark_message_sent(message.id or 0)
                sent += 1
            except Exception as exc:
                error = f"{message.recipient}: {exc}"
                errors.append(error)
                database.mark_message_error(message.id or 0, str(exc))
    return sent, errors


def _safe_signal(lead: Lead) -> str:
    if lead.hiring_signals:
        signal = lead.hiring_signals[0]
        return f"I noticed hiring information mentioning \"{signal}\" on {lead.company_name}'s public website."
    if lead.job_urls:
        return f"I found a careers or jobs page on {lead.company_name}'s public website."
    return f"I was reviewing {lead.company_name}'s public website and wanted to ask about your hiring plans."


ROLE_PREFIXES = {
    "business",
    "careers",
    "career",
    "contact",
    "contactus",
    "hello",
    "hr",
    "hiring",
    "info",
    "jobs",
    "people",
    "recruitment",
    "recruiting",
    "sales",
    "talent",
    "team",
    "work",
}
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def _is_role_business_email(value: str, company_domain: str) -> bool:
    if "\r" in value or "\n" in value:
        return False
    match = EMAIL_PATTERN.fullmatch(value.strip())
    if not match:
        return False
    email_domain = match.group(1).lower().removeprefix("www.")
    company_domain = company_domain.lower().removeprefix("www.")
    if email_domain != company_domain and not email_domain.endswith(f".{company_domain}"):
        return False
    local = value.rsplit("@", 1)[0].lower()
    prefix = re.split(r"[._+-]", local, maxsplit=1)[0]
    return prefix in ROLE_PREFIXES


def _header_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
