from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from .browser_session import create_manual_browser_session
from .config import AppConfig
from .dashboard import serve_dashboard
from .database import Database
from .exporters import export_csv, export_jsonl, sync_google_sheet
from .importers import import_contact_history
from .outreach import create_outreach_drafts, send_approved_messages
from .pipeline import run_pipeline
from .search import load_seed_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="Local-first company lead discovery and approval-based outreach",
    )
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create config.json and .env from examples")
    init.add_argument("--directory", default=".")

    commands.add_parser("init-db", help="Create or migrate the SQLite database")

    run = commands.add_parser("run", help="Discover, crawl, score, and save company leads")
    run.add_argument("--skip-search", action="store_true", help="Use seeds.csv only")
    run.add_argument("--skip-ai", action="store_true", help="Disable AI for this run")

    leads = commands.add_parser("leads", help="List leads")
    leads.add_argument("--min-score", type=int, default=0)
    leads.add_argument("--status")
    leads.add_argument("--limit", type=int, default=50)

    draft = commands.add_parser("draft", help="Generate outreach drafts for qualified leads")
    draft.add_argument("--limit", type=int, default=100)

    messages = commands.add_parser("messages", help="List outreach messages")
    messages.add_argument("--status")
    messages.add_argument("--limit", type=int, default=50)

    approve = commands.add_parser("approve", help="Approve a draft without sending it")
    group = approve.add_mutually_exclusive_group(required=True)
    group.add_argument("--message-id", type=int)
    group.add_argument("--all", action="store_true")

    reject = commands.add_parser("reject", help="Reject a draft")
    reject.add_argument("--message-id", type=int, required=True)

    send = commands.add_parser("send", help="Send approved email drafts through SMTP")
    send.add_argument("--confirm-send", action="store_true")
    send.add_argument("--limit", type=int)

    dnc = commands.add_parser("dnc-add", help="Add an email, domain, or profile key to do-not-contact")
    dnc.add_argument("contact_key")
    dnc.add_argument("--reason", default="Manual do-not-contact entry")

    history = commands.add_parser("import-history", help="Import prior contact history from CSV")
    history.add_argument("file")

    export = commands.add_parser("export", help="Export leads to CSV or JSONL")
    export.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    export.add_argument("--output")
    export.add_argument("--min-score", type=int, default=0)

    commands.add_parser("sync-sheets", help="Upsert leads into a configured Google Sheet")

    dashboard = commands.add_parser("dashboard", help="Open the local review dashboard server")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)

    session = commands.add_parser(
        "browser-session",
        help="Manually create a saved browser session for an allowed, automation-permitted site",
    )
    session.add_argument("--url", required=True)

    commands.add_parser("doctor", help="Validate setup without crawling or sending")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _initialize_project(Path(args.directory))

        config = AppConfig.load(args.config)
        database = Database(config.resolve_path(config.database_path))
        database.initialize()

        if args.command == "init-db":
            print(f"Database ready: {database.path}")
        elif args.command == "run":
            report = run_pipeline(config, skip_search=args.skip_search, skip_ai=args.skip_ai)
            print(json.dumps(asdict(report), indent=2, ensure_ascii=True))
            return 0 if not report.errors else 2
        elif args.command == "leads":
            _print_leads(database, args.status, args.min_score, args.limit)
        elif args.command == "draft":
            drafts = create_outreach_drafts(config, database, args.limit)
            print(f"Drafts ready for review: {len(drafts)}")
        elif args.command == "messages":
            _print_messages(database, args.status, args.limit)
        elif args.command == "approve":
            if args.all:
                drafts = database.list_messages(status="draft", limit=10_000)
                for message in drafts:
                    database.approve_message(message.id or 0)
                print(f"Approved {len(drafts)} drafts. Nothing was sent.")
            else:
                database.approve_message(args.message_id)
                print(f"Approved message {args.message_id}. Nothing was sent.")
        elif args.command == "reject":
            database.reject_message(args.message_id)
            print(f"Rejected message {args.message_id}")
        elif args.command == "send":
            sent, errors = send_approved_messages(
                config,
                database,
                confirm_send=args.confirm_send,
                limit=args.limit,
            )
            print(f"Sent {sent} approved messages")
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 0 if not errors else 2
        elif args.command == "dnc-add":
            database.add_do_not_contact(args.contact_key, args.reason)
            print(f"Added to do-not-contact: {args.contact_key}")
        elif args.command == "import-history":
            report = import_contact_history(database, args.file)
            print(json.dumps(report, indent=2))
        elif args.command == "export":
            output_dir = config.resolve_path(config.output_directory)
            output = Path(args.output) if args.output else output_dir / f"leads.{args.format}"
            if not output.is_absolute():
                output = (config.config_directory / output).resolve()
            if args.format == "csv":
                path = export_csv(database, output, args.min_score)
            else:
                path = export_jsonl(database, output, args.min_score)
            print(f"Exported leads: {path}")
        elif args.command == "sync-sheets":
            updated, appended = sync_google_sheet(config, database)
            print(f"Google Sheet synced: {updated} updated, {appended} appended")
        elif args.command == "dashboard":
            if args.host not in {"127.0.0.1", "localhost"}:
                raise ValueError("Dashboard binds to localhost only")
            serve_dashboard(database, args.host, args.port)
        elif args.command == "browser-session":
            create_manual_browser_session(config, args.url)
        elif args.command == "doctor":
            return _doctor(config, database)
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _initialize_project(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parents[2]
    created = []
    for source_name, target_name in (("config.example.json", "config.json"), (".env.example", ".env")):
        source = package_root / source_name
        target = directory / target_name
        if target.exists():
            print(f"Kept existing: {target}")
            continue
        if not source.exists():
            raise OSError(f"Example file is missing: {source}")
        shutil.copyfile(source, target)
        created.append(target)
    (directory / "data").mkdir(exist_ok=True)
    (directory / "outputs").mkdir(exist_ok=True)
    seed = directory / "data" / "seeds.csv"
    if not seed.exists():
        seed.write_text("url,source\n", encoding="utf-8")
        created.append(seed)
    print("Created: " + ", ".join(str(item) for item in created) if created else "Nothing to create")
    return 0


def _doctor(config: AppConfig, database: Database) -> int:
    checks: list[tuple[str, bool, str]] = []
    seeds = load_seed_results(config.resolve_path(config.seed_file))
    checks.append(("Configuration", True, str(config.config_directory / "config.json")))
    checks.append(("Database", database.path.exists(), str(database.path)))
    checks.append(("Seed URLs", bool(seeds), f"{len(seeds)} rows"))
    checks.append(("Crawler pace", config.crawler.delay_seconds >= 1, f"{config.crawler.delay_seconds}s"))
    if config.ai.enabled:
        key_present = bool(os.environ.get(config.ai.api_key_env, "")) or config.ai.base_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        )
        checks.append(("AI endpoint", key_present, config.ai.base_url))
    if config.smtp.host:
        checks.append(("SMTP username", bool(os.environ.get(config.smtp.username_env, "")), config.smtp.username_env))
        checks.append(("SMTP password", bool(os.environ.get(config.smtp.password_env, "")), config.smtp.password_env))
    if config.crawler.use_browser:
        try:
            import playwright  # noqa: F401
            browser_ok = True
        except ImportError:
            browser_ok = False
        checks.append(("Browser package", browser_ok, "playwright"))
    for name, passed, detail in checks:
        print(f"{'OK' if passed else 'WARN':4}  {name:20} {detail}")
    warnings = sum(not passed for _, passed, _ in checks)
    print(f"\nSetup check complete: {warnings} warning(s)")
    return 0


def _print_leads(database: Database, status: str | None, min_score: int, limit: int) -> None:
    rows = database.list_leads(status=status, min_score=min_score, limit=limit)
    print(f"{'ID':>4} {'SCORE':>5} {'STATUS':<11} {'COMPANY':<30} DOMAIN")
    for lead in rows:
        print(f"{lead.id or 0:>4} {lead.score:>5} {lead.status:<11.11} {lead.company_name[:30]:<30} {lead.domain}")
    print(f"\n{len(rows)} lead(s)")


def _print_messages(database: Database, status: str | None, limit: int) -> None:
    rows = database.list_messages(status=status, limit=limit)
    print(f"{'ID':>4} {'STATUS':<10} {'CHANNEL':<8} {'RECIPIENT':<36} SUBJECT")
    for message in rows:
        print(
            f"{message.id or 0:>4} {message.status:<10.10} {message.channel:<8.8} "
            f"{message.recipient[:36]:<36} {message.subject[:50]}"
        )
    print(f"\n{len(rows)} message(s)")


if __name__ == "__main__":
    raise SystemExit(main())
