from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import connect, init_db
from .extractors import DETERMINISTIC_EXTRACTOR_VERSION, run_deterministic_extractors
from .ingest import ingest_hermes_state_db, ingest_path
from .llm import health as llm_health
from .mine import run_mine_cycle, write_schedule_templates
from .report import write_findings_report, write_pilot_report
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
    queued = enqueue_work(conn, extractor_version=DETERMINISTIC_EXTRACTOR_VERSION)
    summary = run_deterministic_extractors(conn)
    output: dict[str, object] = {"stale_reset": reset, "queued": queued, "records_created": summary.records_created}
    print(json.dumps(output, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    if args.pilot:
        report = write_pilot_report(conn, args.output, run_id=args.run_id, include_quotes=args.include_quotes)
    else:
        from .cie import init_cie_tables

        init_cie_tables(conn)
        report = write_findings_report(conn, args.output, limit_per_family=args.limit_per_family)
    print(json.dumps({"report": str(report)}, indent=2))
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    conn = _conn(args.db)
    result = run_mine_cycle(
        conn,
        state_db=args.state_db,
        limit=args.limit,
        extractor_version=args.extractor_version,
        model=args.model,
        base_url=args.base_url,
        xai_api_key_env=args.xai_api_key_env,
        xai_reasoning_effort=args.xai_reasoning_effort,
        pass_strategy=args.pass_strategy,
        max_window_tokens=args.max_window_tokens,
        overlap_tokens=args.overlap_tokens,
        max_prompt_tokens=args.max_prompt_tokens,
        timeout=args.timeout,
        llm_max_tokens=args.llm_max_tokens,
        cost_cap_usd=args.cost_cap_usd,
        max_model_calls=args.max_model_calls,
        env_file=args.env_file,
        no_preflight=args.no_preflight,
        report_path=args.report,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_schedule_template(args: argparse.Namespace) -> int:
    paths = write_schedule_templates(args.output_dir, mine_command=args.mine_command)
    print(json.dumps({"schedule_templates": [str(path) for path in paths], "enabled": False}, indent=2))
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
    extract.set_defaults(func=cmd_extract)

    status = sub.add_parser("status")
    status.add_argument("--check-llm", action="store_true")
    status.add_argument("--llm-url", default="http://127.0.0.1:8080")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report")
    report.add_argument("--run-id", default="pilot-001")
    report.add_argument("--output", type=Path, default=Path("runtime/findings-report.md"))
    report.add_argument("--include-quotes", action="store_true")
    report.add_argument("--limit-per-family", type=int, default=None)
    report.add_argument("--pilot", action="store_true", help="Write the retired deterministic pilot report instead of the CIE findings register report")
    report.set_defaults(func=cmd_report)

    mine = sub.add_parser("mine")
    mine.add_argument("--state-db", type=Path, default=Path.home() / ".hermes" / "state.db")
    mine.add_argument("--limit", type=int, default=20)
    mine.add_argument("--extractor-version", default="cie-v1-qwen3.6-opus-fewshot-20260612")
    mine.add_argument("--model", default="grok-4.3")
    mine.add_argument("--base-url", default="https://api.x.ai/v1")
    mine.add_argument("--xai-api-key-env", default="XAI_API_KEY")
    mine.add_argument("--xai-reasoning-effort", choices=["none", "low", "medium", "high"], default="high")
    mine.add_argument("--pass-strategy", choices=["per_family", "combined"], default="per_family")
    mine.add_argument("--max-window-tokens", type=int, default=3500)
    mine.add_argument("--overlap-tokens", type=int, default=600)
    mine.add_argument("--max-prompt-tokens", type=int, default=7600)
    mine.add_argument("--timeout", type=int, default=600)
    mine.add_argument("--llm-max-tokens", type=int, default=4096)
    mine.add_argument("--cost-cap-usd", type=float, default=10.0)
    mine.add_argument("--max-model-calls", type=int, default=None)
    mine.add_argument("--env-file", default=".env")
    mine.add_argument("--no-preflight", action="store_true")
    mine.add_argument("--report", type=Path, default=Path("runtime/findings-report.md"))
    mine.set_defaults(func=cmd_mine)

    schedule = sub.add_parser("schedule-template")
    schedule.add_argument("--output-dir", type=Path, default=Path("runtime/schedule"))
    schedule.add_argument(
        "--mine-command",
        default="cd /path/to/leon-pattern-miner && uv run miner --db runtime/miner.db mine --limit 20 --cost-cap-usd 10 --xai-reasoning-effort high --pass-strategy per_family --report runtime/findings-report.md",
    )
    schedule.set_defaults(func=cmd_schedule_template)

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
