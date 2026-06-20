from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from .sensitivity import mask_sensitive


def _table_counts(conn: sqlite3.Connection, column: str) -> Counter[str]:
    rows = conn.execute(f"select {column} as k, count(*) as c from records group by {column} order by c desc").fetchall()
    return Counter({row["k"]: row["c"] for row in rows})


def _append_record(lines: list[str], row: sqlite3.Row, *, include_quotes: bool) -> None:
    lines.append(f"### {row['stream']} / {row['pattern_type']}")
    lines.append("")
    lines.append(f"- summary: {row['summary']}")
    lines.append(f"- sensitivity: {row['sensitivity']}")
    lines.append(f"- recommended_sink: {row['recommended_sink']}")
    if include_quotes:
        if row["sensitivity"] != "internal":
            lines.append(f"> [SUPPRESSED_{row['sensitivity'].upper()}_QUOTE]")
        else:
            evidence = json.loads(row["evidence_json"])
            for ev in evidence[:1]:
                quote, _ = mask_sensitive(ev["quote"])
                lines.append(f"> {quote[:700]}")
    lines.append("")


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
    lines.extend(["", "## Error classes", ""])
    for row in conn.execute("select error_class, count(*) as c from errors group by error_class order by c desc"):
        lines.append(f"- {row['error_class']}: {row['c']}")

    lines.extend(["", "## Examples by top pattern", ""])
    for pattern, _count in pattern_counts.most_common(10):
        rows = conn.execute(
            """
            select stream, pattern_type, summary, evidence_json, sensitivity, recommended_sink
            from records
            where pattern_type=?
            order by case when sensitivity='internal' then 0 else 1 end, confidence desc
            limit ?
            """,
            (pattern, max_examples),
        ).fetchall()
        for row in rows:
            _append_record(lines, row, include_quotes=include_quotes)

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_findings_report(
    conn: sqlite3.Connection,
    path: str | Path = "runtime/findings-report.md",
    *,
    limit_per_family: int | None = None,
) -> Path:
    """Write a human-readable CIE register report grouped by family and frequency."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        select family, codebook_code, statement, occurrence_count, last_seen, created_at
        from cie_records
        where quote_verified=1
        order by family, occurrence_count desc, coalesce(last_seen, created_at) desc, statement
        """
    ).fetchall()
    total_rows = conn.execute("select count(*) from cie_records where quote_verified=1").fetchone()[0]
    total_occurrences = conn.execute(
        "select coalesce(sum(occurrence_count), 0) from cie_records where quote_verified=1"
    ).fetchone()[0]
    collapsed = int(total_occurrences or 0) - int(total_rows or 0)

    lines = [
        "# Findings register report",
        "",
        "Local-only report from `cie_records`. Statements are quote-verified before entering the register.",
        "",
        "## Summary",
        "",
        f"- rows: {total_rows}",
        f"- total_occurrences: {total_occurrences}",
        f"- dedup_collapsed_occurrences: {collapsed}",
        "",
    ]
    current_family: str | None = None
    emitted_for_family = 0
    for row in rows:
        family = str(row["family"])
        if family != current_family:
            current_family = family
            emitted_for_family = 0
            lines.extend(
                [
                    f"## {family}",
                    "",
                    "| statement | count | last_seen |",
                    "|---|---:|---|",
                ]
            )
        if limit_per_family is not None and emitted_for_family >= limit_per_family:
            continue
        statement = str(row["statement"] or "").replace("|", "\\|").replace("\n", " ")
        last_seen = row["last_seen"] or row["created_at"] or ""
        lines.append(f"| {statement} | {row['occurrence_count']} | {last_seen} |")
        emitted_for_family += 1
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
