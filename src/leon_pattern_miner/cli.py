from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import connect, init_db
from .extractors import run_deterministic_extractors
from .ingest import ingest_hermes_state_db, ingest_path
from .llm import health as llm_health
from .llm_extractors import DEFAULT_LLM_EXTRACTOR_VERSION, run_llm_extractors
from .report import write_pilot_report
from .runner import approve_pilot, enqueue_work, reset_stale_running_work, status_snapshot

DEFAULT_DB = Path("runtime/miner.db")


def _conn(path: Path):
    conn = connect(path)
    init_db(conn)
    return conn


def cmd_ingest(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    total_sessions = total_turns = errors = 0
    paths: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.json")))
            paths.extend(sorted(p.rglob("*.jsonl")))
        else:
            paths.append(p)
    for p in paths:
        if "leon-pattern-miner" in str(p):
            continue
        result = ingest_path(conn, p)
        total_sessions += result.sessions_ingested
        total_turns += result.turns_ingested
        errors += result.errors
    print(json.dumps({"sessions_ingested": total_sessions, "turns_ingested": total_turns, "errors": errors}, indent=2))
    return 0


def cmd_ingest_hermes(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    result = ingest_hermes_state_db(conn, args.state_db, limit=args.limit)
    print(
        json.dumps(
            {
                "sessions_ingested": result.sessions_ingested,
                "turns_ingested": result.turns_ingested,
                "errors": result.errors,
                "state_db": str(args.state_db),
            },
            indent=2,
        )
    )
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    if args.full_corpus:
        approved = status_snapshot(conn)["pilot_approved"]
        if not approved:
            print("BLOCKED: full-corpus extraction requires approved 20-session pilot")
            return 2
    reset = reset_stale_running_work(conn, older_than_minutes=args.stale_minutes)
    queued = enqueue_work(conn, extractor_version="deterministic-v1")
    summary = run_deterministic_extractors(conn)
    output: dict[str, object] = {"stale_reset": reset, "queued": queued, "records_created": summary.records_created}
    if args.use_llm:
        llm_summary = run_llm_extractors(
            conn,
            base_url=args.llm_url,
            extractor_version=args.llm_extractor_version,
            max_sessions=args.llm_max_sessions,
            timeout=args.llm_timeout,
        )
        output["llm"] = {
            "extractor_version": args.llm_extractor_version,
            "sessions_processed": llm_summary.sessions_processed,
            "records_created": llm_summary.records_created,
            "errors": llm_summary.errors,
        }
    print(json.dumps(output, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    report = write_pilot_report(conn, args.output, run_id=args.run_id, include_quotes=args.include_quotes)
    print(json.dumps({"report": str(report)}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    snap = status_snapshot(conn)
    if args.check_llm:
        h = llm_health(args.llm_url)
        snap["llm"] = {"ok": h.ok, "detail": h.detail}
    print(json.dumps(snap, indent=2, sort_keys=True))
    return 0


def cmd_approve_pilot(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    approve_pilot(conn, run_id=args.run_id, reviewer=args.reviewer, notes=args.notes)
    print(json.dumps({"pilot_approved": True, "run_id": args.run_id, "reviewer": args.reviewer}, indent=2))
    return 0


def cmd_retry_failed(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    cur = conn.execute("update work_items set status='pending', last_error=null where status='failed'")
    conn.commit()
    print(json.dumps({"retry_queued": cur.rowcount}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miner")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("paths", nargs="+")
    ingest.set_defaults(func=cmd_ingest)

    ingest_hermes = sub.add_parser("ingest-hermes")
    ingest_hermes.add_argument("--state-db", type=Path, default=Path.home() / ".hermes" / "state.db")
    ingest_hermes.add_argument("--limit", type=int, default=20)
    ingest_hermes.set_defaults(func=cmd_ingest_hermes)

    extract = sub.add_parser("extract")
    extract.add_argument("--full-corpus", action="store_true")
    extract.add_argument("--stale-minutes", type=int, default=30)
    extract.add_argument("--use-llm", action="store_true")
    extract.add_argument("--llm-url", default="http://127.0.0.1:8080")
    extract.add_argument("--llm-extractor-version", default=DEFAULT_LLM_EXTRACTOR_VERSION)
    extract.add_argument("--llm-max-sessions", type=int, default=None)
    extract.add_argument("--llm-timeout", type=int, default=120)
    extract.set_defaults(func=cmd_extract)

    status = sub.add_parser("status")
    status.add_argument("--check-llm", action="store_true")
    status.add_argument("--llm-url", default="http://127.0.0.1:8080")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report")
    report.add_argument("--run-id", default="pilot-001")
    report.add_argument("--output", type=Path, default=Path("reports/pilot-001.md"))
    report.add_argument("--include-quotes", action="store_true")
    report.set_defaults(func=cmd_report)

    approve = sub.add_parser("approve-pilot")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--reviewer", default="fable")
    approve.add_argument("--notes", default="ACCEPT")
    approve.set_defaults(func=cmd_approve_pilot)

    retry = sub.add_parser("retry-failed")
    retry.set_defaults(func=cmd_retry_failed)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
