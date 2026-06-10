from __future__ import annotations

import json
import sqlite3
from datetime import datetime

STREAMS = ("steering", "behavior", "methodology")


def enqueue_work(conn: sqlite3.Connection, *, extractor_version: str) -> int:
    created = 0
    sessions = conn.execute("select session_id from sessions where status in ('normalized','extracted','verified')").fetchall()
    for row in sessions:
        for stream in STREAMS:
            cur = conn.execute(
                "insert or ignore into work_items(session_id, stream, extractor_version) values (?, ?, ?)",
                (row["session_id"], stream, extractor_version),
            )
            created += cur.rowcount
    conn.commit()
    return created


def reset_stale_running_work(conn: sqlite3.Connection, *, older_than_minutes: int = 30) -> int:
    cur = conn.execute(
        """
        update work_items
        set status='pending', last_error=coalesce(last_error, 'reset stale running item'), started_at=null
        where status='running'
          and started_at is not null
          and started_at < datetime('now', ?)
        """,
        (f"-{older_than_minutes} minutes",),
    )
    conn.commit()
    return cur.rowcount


def work_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("select status, count(*) as c from work_items group by status").fetchall()
    return {row["status"]: row["c"] for row in rows}


def approve_pilot(conn: sqlite3.Connection, *, run_id: str, reviewer: str, notes: str) -> None:
    conn.execute(
        """
        insert into approvals(key, value, approved_by, approved_at, notes)
        values ('pilot_approved', 'true', ?, ?, ?)
        on conflict(key) do update set
            value=excluded.value,
            approved_by=excluded.approved_by,
            approved_at=excluded.approved_at,
            notes=excluded.notes
        """,
        (reviewer, datetime.utcnow().isoformat(timespec="seconds") + "Z", json.dumps({"run_id": run_id, "notes": notes})),
    )
    conn.commit()


def pilot_is_approved(conn: sqlite3.Connection) -> bool:
    row = conn.execute("select value from approvals where key='pilot_approved'").fetchone()
    return bool(row and row["value"] == "true")


def status_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    def scalar(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "sessions": scalar("select count(*) from sessions"),
        "turns": scalar("select count(*) from turns"),
        "records": scalar("select count(*) from records"),
        "errors": scalar("select count(*) from errors"),
        "work_items": work_status_counts(conn),
        "pilot_approved": pilot_is_approved(conn),
    }
