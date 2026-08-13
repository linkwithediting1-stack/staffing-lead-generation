from __future__ import annotations

import html
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .database import Database


CSS = """
:root { color-scheme: light; --ink:#15201b; --muted:#637069; --line:#d7ddd9; --paper:#f8faf8;
  --white:#fff; --green:#17653a; --amber:#9a5800; --red:#9b2c2c; --blue:#2458a6; }
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font:14px/1.45 Arial, sans-serif; }
header { background:var(--white); border-bottom:1px solid var(--line); padding:18px 24px 14px; }
h1 { font-size:21px; margin:0 0 12px; letter-spacing:0; }
nav { display:flex; gap:4px; }
nav a { color:var(--muted); padding:7px 10px; text-decoration:none; border-bottom:2px solid transparent; }
nav a.active { color:var(--ink); border-color:var(--green); font-weight:700; }
main { padding:18px 24px 40px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); border:1px solid var(--line); background:var(--white); margin-bottom:18px; }
.stat { padding:12px 14px; border-right:1px solid var(--line); min-height:68px; }
.stat:last-child { border-right:0; }
.stat strong { display:block; font-size:22px; }
.stat span { color:var(--muted); font-size:12px; }
.toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 10px; }
.toolbar h2 { font-size:16px; margin:0; }
.table-wrap { overflow:auto; border:1px solid var(--line); background:var(--white); }
table { border-collapse:collapse; min-width:1050px; width:100%; }
th, td { border-bottom:1px solid #e8ece9; padding:9px 10px; text-align:left; vertical-align:top; }
th { background:#eef2ef; color:#39443e; font-size:12px; position:sticky; top:0; }
td small { color:var(--muted); display:block; margin-top:3px; }
a { color:var(--blue); }
.score { font-weight:700; }
.status { font-size:12px; font-weight:700; text-transform:uppercase; }
.status.replied,.status.approved,.status.sent { color:var(--green); }
.status.error,.status.rejected,.status.unreachable { color:var(--red); }
.status.review,.status.draft { color:var(--amber); }
form { display:inline; }
button { border:1px solid #aab5ae; background:white; color:var(--ink); padding:5px 8px; cursor:pointer; margin:1px; }
button:hover { border-color:var(--green); color:var(--green); }
button.danger:hover { border-color:var(--red); color:var(--red); }
.message { white-space:pre-wrap; min-width:360px; max-width:560px; }
.empty { padding:26px; color:var(--muted); }
@media (max-width:700px) { header,main { padding-left:12px; padding-right:12px; } .stats { grid-template-columns:1fr 1fr; } }
"""


class DashboardServer(ThreadingHTTPServer):
    database: Database
    csrf_token: str


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        query = parse_qs(urlsplit(self.path).query)
        view = query.get("view", ["leads"])[0]
        body = self._render_page(view)
        self._send_html(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10_000:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        if form.get("csrf", [""])[0] != self.server.csrf_token:
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid CSRF token")
            return
        parts = [part for part in urlsplit(self.path).path.split("/") if part]
        try:
            if len(parts) == 3 and parts[0] == "messages":
                message_id = int(parts[1])
                if parts[2] == "approve":
                    self.server.database.approve_message(message_id)
                elif parts[2] == "reject":
                    self.server.database.reject_message(message_id)
                else:
                    raise ValueError("Unknown message action")
                destination = "/?view=messages"
            elif len(parts) == 3 and parts[0] == "leads":
                lead_id = int(parts[1])
                action_status = {"approve": "approved", "reject": "rejected"}.get(parts[2])
                if not action_status:
                    raise ValueError("Unknown lead action")
                self.server.database.set_lead_status(lead_id, action_status)
                destination = "/?view=leads"
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except (ValueError, TypeError) as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", destination)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return

    def _render_page(self, view: str) -> str:
        view = "messages" if view == "messages" else "leads"
        stats = self.server.database.stats()
        stats_html = "".join(
            f'<div class="stat"><strong>{int(value)}</strong><span>{_pretty(key)}</span></div>'
            for key, value in sorted(stats.items())
            if key in {"leads_total", "leads_review", "leads_contacted", "leads_replied", "messages_draft", "messages_approved"}
        )
        table = self._render_messages() if view == "messages" else self._render_leads()
        lead_active = "active" if view == "leads" else ""
        message_active = "active" if view == "messages" else ""
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LeadGen AI Review</title><style>{CSS}</style></head>
<body><header><h1>LeadGen AI Review</h1><nav>
<a class="{lead_active}" href="/?view=leads">Company leads</a>
<a class="{message_active}" href="/?view=messages">Outreach drafts</a>
</nav></header><main><section class="stats">{stats_html}</section>{table}</main></body></html>"""

    def _render_leads(self) -> str:
        leads = self.server.database.list_leads(limit=250)
        if not leads:
            return '<div class="empty">No leads yet. Run the discovery pipeline first.</div>'
        rows = []
        for lead in leads:
            evidence = ", ".join(lead.hiring_signals) or "No hiring phrase captured"
            email = lead.emails[0] if lead.emails else ""
            actions = ""
            if lead.status in {"new", "review"}:
                actions = self._post_button(f"/leads/{lead.id}/approve", "Approve") + self._post_button(
                    f"/leads/{lead.id}/reject", "Reject", danger=True
                )
            rows.append(
                "<tr>"
                f'<td><strong>{_h(lead.company_name)}</strong><small>{_h(lead.domain)}</small></td>'
                f'<td class="score">{lead.score}</td>'
                f'<td><span class="status {_h(lead.status)}">{_h(lead.status)}</span></td>'
                f'<td>{_h(lead.location or "-")}<small>{_h(lead.industry or "-")}</small></td>'
                f'<td>{_h(evidence)}<small>{_h(lead.score_reason)}</small></td>'
                f'<td>{_h(email or "-")}</td>'
                f'<td><a href="{_ha(lead.website_url)}" target="_blank" rel="noreferrer">Website</a></td>'
                f"<td>{actions}</td></tr>"
            )
        return (
            '<div class="toolbar"><h2>Qualified company leads</h2><span>Highest score first</span></div>'
            '<div class="table-wrap"><table><thead><tr><th>Company</th><th>Score</th><th>Status</th>'
            '<th>Target fit</th><th>Evidence</th><th>Contact route</th><th>Source</th><th>Review</th>'
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )

    def _render_messages(self) -> str:
        messages = self.server.database.list_messages(limit=250)
        if not messages:
            return '<div class="empty">No drafts yet. Generate drafts after reviewing qualified leads.</div>'
        rows = []
        for message in messages:
            lead = self.server.database.get_lead(message.lead_id)
            actions = ""
            if message.status == "draft":
                actions = self._post_button(f"/messages/{message.id}/approve", "Approve") + self._post_button(
                    f"/messages/{message.id}/reject", "Reject", danger=True
                )
            rows.append(
                "<tr>"
                f"<td>{_h(lead.company_name if lead else str(message.lead_id))}</td>"
                f"<td>{_h(message.recipient)}</td>"
                f'<td><span class="status {_h(message.status)}">{_h(message.status)}</span></td>'
                f'<td><strong>{_h(message.subject)}</strong><div class="message">{_h(message.body)}</div></td>'
                f"<td>{actions}</td></tr>"
            )
        return (
            '<div class="toolbar"><h2>Outreach review queue</h2><span>Approval does not send email</span></div>'
            '<div class="table-wrap"><table><thead><tr><th>Company</th><th>Recipient</th><th>Status</th>'
            f"<th>Draft</th><th>Review</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )

    def _post_button(self, action: str, label: str, danger: bool = False) -> str:
        css_class = ' class="danger"' if danger else ""
        return (
            f'<form method="post" action="{_ha(action)}">'
            f'<input type="hidden" name="csrf" value="{_ha(self.server.csrf_token)}">'
            f"<button{css_class} type=\"submit\">{_h(label)}</button></form>"
        )

    def _send_html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def serve_dashboard(database: Database, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = DashboardServer((host, port), DashboardHandler)
    server.database = database
    server.csrf_token = secrets.token_urlsafe(24)
    print(f"LeadGen AI dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _h(value) -> str:
    return html.escape(str(value), quote=False)


def _ha(value) -> str:
    return html.escape(str(value), quote=True)


def _pretty(value: str) -> str:
    return value.replace("_", " ").title()
