from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from leon_pattern_miner.cie import DEFAULT_CIE_EXTRACTOR_VERSION, init_cie_tables, run_cie_harness
from leon_pattern_miner.db import connect


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CIE v1 Qwen extraction harness")
    parser.add_argument("--db", default="runtime/miner.db")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--extractor-version", default=DEFAULT_CIE_EXTRACTOR_VERSION)
    parser.add_argument("--max-sessions", type=int)
    parser.add_argument("--session-id", action="append", dest="session_ids")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--llm-max-tokens", type=int, default=3072)
    parser.add_argument("--max-window-tokens", type=int, default=3500)
    parser.add_argument("--overlap-tokens", type=int, default=600)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--combined-pass", action="store_true", help="Run one all-code pass per window instead of separate focused family passes")
    parser.add_argument("--summary-json", default="")
    return parser


def progress_snapshot(conn: sqlite3.Connection, extractor_version: str) -> dict:
    init_cie_tables(conn)
    rows = conn.execute(
        """
        select family, status, count(*) as n, coalesce(sum(records_created),0) as records_created,
               coalesce(sum(records_rejected),0) as records_rejected
        from cie_window_runs
        where extractor_version=?
        group by family, status
        order by family, status
        """,
        (extractor_version,),
    ).fetchall()
    record_count = conn.execute(
        "select count(*) from cie_records where extractor_version=?", (extractor_version,)
    ).fetchone()[0]
    sessions_seen = conn.execute(
        "select count(distinct session_id) from cie_window_runs where extractor_version=?",
        (extractor_version,),
    ).fetchone()[0]
    return {
        "extractor_version": extractor_version,
        "sessions_seen": sessions_seen,
        "records": record_count,
        "window_runs": [dict(row) for row in rows],
    }


def main() -> None:
    args = build_parser().parse_args()
    conn = connect(Path(args.db))
    summary = run_cie_harness(
        conn,
        extractor_version=args.extractor_version,
        base_url=args.base_url,
        max_sessions=args.max_sessions,
        session_ids=args.session_ids,
        timeout=args.timeout,
        llm_max_tokens=args.llm_max_tokens,
        max_window_tokens=args.max_window_tokens,
        overlap_tokens=args.overlap_tokens,
        combined_pass=args.combined_pass,
        resume=not args.no_resume,
    )
    snap = progress_snapshot(conn, args.extractor_version)
    out = {"summary": summary.__dict__, "progress": snap}
    text = json.dumps(out, indent=2)
    if args.summary_json:
        Path(args.summary_json).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
