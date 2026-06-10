from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from .sensitivity import mask_sensitive


def _table_counts(conn: sqlite3.Connection, column: str) -> Counter[str]:
    rows = conn.execute(f"select {column} as k, count(*) as c from records group by {column} order by c desc").fetchall()
    return Counter({row["k"]: row["c"] for row in rows})


def write_pilot_report(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    run_id: str,
    include_quotes: bool = False,
    max_examples: int = 20,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sessions = conn.execute("select count(*) from sessions where status != 'error'").fetchone()[0]
    turns = conn.execute("select count(*) from turns").fetchone()[0]
    records = conn.execute("select count(*) from records").fetchone()[0]
    errors = conn.execute("select count(*) from errors").fetchone()[0]
    stream_counts = _table_counts(conn, "stream")
    pattern_counts = _table_counts(conn, "pattern_type")
    sensitivity_counts = _table_counts(conn, "sensitivity")

    lines = [
        f"# Pilot Report — {run_id}",
        "",
        "Local-only report. Do not commit if it includes real transcript quotes.",
        "",
        "## Counts",
        "",
        f"- sessions: {sessions}",
        f"- turns: {turns}",
        f"- records: {records}",
        f"- errors: {errors}",
        "",
        "## Stream counts",
        "",
    ]
    for key, value in stream_counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Pattern counts", ""])
    for key, value in pattern_counts.most_common(20):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Sensitivity counts", ""])
    for key, value in sensitivity_counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Example records", ""])

    for row in conn.execute(
        "select stream, pattern_type, summary, evidence_json, sensitivity, recommended_sink from records order by stream, pattern_type limit ?",
        (max_examples,),
    ):
        lines.append(f"### {row['stream']} / {row['pattern_type']}")
        lines.append("")
        lines.append(f"- summary: {row['summary']}")
        lines.append(f"- sensitivity: {row['sensitivity']}")
        lines.append(f"- recommended_sink: {row['recommended_sink']}")
        if include_quotes:
            evidence = json.loads(row["evidence_json"])
            for ev in evidence[:3]:
                quote, _ = mask_sensitive(ev["quote"])
                lines.append(f"> {quote[:1000]}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
