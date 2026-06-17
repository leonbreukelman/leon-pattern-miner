from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists sessions (
    session_id text primary key,
    source_path text not null,
    format text not null,
    turn_count integer not null default 0,
    content_hash text,
    ingested_at text default (datetime('now')),
    status text not null default 'pending',
    error_detail text
);

create table if not exists turns (
    turn_id text primary key,
    session_id text not null references sessions(session_id),
    idx integer not null,
    actor text not null,
    ts text,
    text text not null,
    tool_name text,
    char_offset_start integer not null default 0,
    char_offset_end integer not null default 0,
    unique(session_id, idx)
);

create table if not exists work_items (
    id integer primary key autoincrement,
    session_id text not null references sessions(session_id),
    stream text not null,
    extractor_version text not null,
    status text not null default 'pending',
    attempts integer not null default 0,
    last_error text,
    started_at text,
    finished_at text,
    unique(session_id, stream, extractor_version)
);

create table if not exists records (
    record_id text primary key,
    session_id text not null references sessions(session_id),
    stream text not null,
    pattern_type text not null,
    summary text not null,
    evidence_json text not null,
    scope text not null default 'session',
    actor text not null,
    confidence real not null default 0.5,
    sensitivity text not null default 'internal',
    verification_status text not null default 'machine_verified',
    extractor text not null,
    extractor_version text not null,
    recommended_sink text not null default 'report_only',
    created_at text default (datetime('now'))
);

create table if not exists runs (
    run_id text primary key,
    started_at text default (datetime('now')),
    finished_at text,
    config_json text,
    status text not null default 'running',
    notes text
);

create table if not exists errors (
    id integer primary key autoincrement,
    work_item_id integer,
    session_id text,
    attempt integer,
    error_class text not null,
    payload_excerpt text,
    ts text default (datetime('now'))
);

create table if not exists approvals (
    key text primary key,
    value text not null,
    approved_by text,
    approved_at text,
    notes text
);

insert or ignore into approvals(key, value) values ('pilot_approved', 'false');
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma foreign_keys=on")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
