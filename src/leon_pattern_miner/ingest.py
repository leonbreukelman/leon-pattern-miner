from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IngestResult:
    sessions_ingested: int = 0
    turns_ingested: int = 0
    errors: int = 0


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _actor_from_role(role: str | None) -> str:
    role = (role or "").lower()
    if role in {"user", "human"}:
        return "leon"
    if role in {"assistant", "agent"}:
        return "agent"
    if role == "tool":
        return "tool"
    if role == "system":
        return "system"
    return role or "unknown"


def _text_from_message(msg: dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _should_skip_message(role: str | None, tool_name: str | None, text: str) -> bool:
    lower = (text or "").lower()
    if not lower.strip() and not tool_name:
        return True
    if role == "system":
        return True
    if "[context compaction" in lower or "context compaction — reference only" in lower:
        return True
    if lower.startswith("[important:") or "[important: background process" in lower or "[important: the user has invoked" in lower:
        return True
    if lower.startswith("[your active task list was preserved across context compression]"):
        return True
    if "<available_skills>" in lower or "conversation-archive-mining/skill.md" in lower:
        return True
    if tool_name in {"skill_view", "skills_list"}:
        return True
    if role == "tool" and tool_name not in {"terminal", "process", "browser_console"}:
        return True
    if "tool definitions" in lower and "namespace" in lower:
        return True
    return False


def _load_messages(path: Path) -> tuple[str, list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        messages = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL parse error on line {line_no}: {exc}") from exc
        return "jsonl", messages

    if suffix == ".json":
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON parse error: {exc}") from exc
        if isinstance(obj, list):
            return "json", obj
        for key in ("messages", "conversation", "turns"):
            val = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(val, list):
                return "json", val
        raise ValueError("JSON session did not contain a messages/conversation/turns list")

    raise ValueError(f"Unsupported session file extension: {suffix}")


def _store_messages(
    conn: sqlite3.Connection,
    *,
    sid: str,
    source_path: str,
    fmt: str,
    messages: list[dict[str, Any]],
    content_hash: str,
) -> IngestResult:
    conn.execute("delete from turns where session_id=?", (sid,))
    conn.execute(
        "insert or replace into sessions(session_id, source_path, format, turn_count, content_hash, status, error_detail) values (?, ?, ?, ?, ?, 'normalized', null)",
        (sid, source_path, fmt, len(messages), content_hash),
    )
    cursor = 0
    inserted = 0
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            msg = {"role": "unknown", "content": str(msg)}
        text = _text_from_message(msg)
        role = msg.get("role") or msg.get("actor") or msg.get("type")
        tool_name = msg.get("tool_name") or msg.get("name") if isinstance(msg, dict) else None
        if _should_skip_message(str(role or "").lower(), tool_name, text):
            continue
        actor = _actor_from_role(role)
        start = cursor
        end = start + len(text)
        turn_id = f"{sid}:{idx}"
        conn.execute(
            "insert into turns(turn_id, session_id, idx, actor, ts, text, tool_name, char_offset_start, char_offset_end) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, sid, idx, actor, msg.get("timestamp") or msg.get("ts"), text, tool_name, start, end),
        )
        cursor = end + 1
        inserted += 1
    conn.execute("update sessions set turn_count=? where session_id=?", (inserted, sid))
    conn.commit()
    return IngestResult(sessions_ingested=1, turns_ingested=inserted)


def ingest_path(conn: sqlite3.Connection, path: str | Path, *, session_id: str | None = None) -> IngestResult:
    path = Path(path)
    data = path.read_bytes()
    content_hash = _hash_bytes(data)
    sid = session_id or f"file:{content_hash[:16]}"

    existing = conn.execute("select content_hash, status from sessions where session_id=?", (sid,)).fetchone()
    if existing and existing["content_hash"] == content_hash and existing["status"] != "error":
        return IngestResult()

    try:
        fmt, messages = _load_messages(path)
    except Exception as exc:
        conn.execute(
            "insert or replace into sessions(session_id, source_path, format, turn_count, content_hash, status, error_detail) values (?, ?, ?, 0, ?, 'error', ?)",
            (sid, str(path), path.suffix.lower().lstrip(".") or "unknown", content_hash, str(exc)),
        )
        conn.commit()
        return IngestResult(errors=1)

    return _store_messages(conn, sid=sid, source_path=str(path), fmt=fmt, messages=messages, content_hash=content_hash)


def ingest_hermes_state_db(
    conn: sqlite3.Connection,
    state_db_path: str | Path,
    *,
    limit: int = 20,
    exclude_substring: str = "leon-pattern-miner,leon pattern miner,pattern miner,pattern-miner,conversation miner,conversation mining,conversation-mining,pilot-00,local conversation mining",
) -> IngestResult:
    """Ingest recent Hermes sessions directly from ~/.hermes/state.db.

    Real transcript-derived records stay in this miner's ignored local DB/reports. The default
    excludes this project to avoid feedback loops.
    """
    state_db_path = Path(state_db_path)
    source = sqlite3.connect(f"file:{state_db_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    session_cols = {row["name"] for row in source.execute("pragma table_info(sessions)")}
    message_cols = {row["name"] for row in source.execute("pragma table_info(messages)")}
    cwd_expr = "coalesce(cwd, '')" if "cwd" in session_cols else "''"
    active_filter = "and active=1" if "active" in message_cols else ""
    rows = source.execute(
        f"""
        select id, source, coalesce(title, '') as title, {cwd_expr} as cwd,
               started_at, message_count
        from sessions
        where message_count > 0
        order by started_at desc
        """
    ).fetchall()
    ingested = turns = errors = 0
    exclude_terms = [term.strip().lower() for term in exclude_substring.split(",") if term.strip()]
    for sess in rows:
        haystack = "\n".join(str(sess[k] or "") for k in sess.keys()).lower()
        if any(term in haystack for term in exclude_terms):
            continue
        if limit > 0 and ingested >= limit:
            break
        messages = []
        for msg in source.execute(
            f"select role, content, tool_name, timestamp from messages where session_id=? {active_filter} order by id",
            (sess["id"],),
        ):
            content = msg["content"] or ""
            role = msg["role"]
            tool_name = msg["tool_name"]
            if _should_skip_message(role, tool_name, content):
                continue
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "tool_name": tool_name,
                    "timestamp": str(msg["timestamp"]),
                }
            )
        if not messages:
            continue
        message_haystack = "\n".join(str(m.get("content") or "") for m in messages).lower()
        if any(term in message_haystack for term in exclude_terms):
            continue
        content_hash = hashlib.sha256(
            json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        sid = f"hermes:{sess['id']}"
        existing = conn.execute("select content_hash from sessions where session_id=?", (sid,)).fetchone()
        if existing and existing["content_hash"] == content_hash:
            continue
        try:
            result = _store_messages(
                conn,
                sid=sid,
                source_path=f"hermes-state:{sess['source']}:{sess['title'] or sess['id']}",
                fmt="hermes_state_db",
                messages=messages,
                content_hash=content_hash,
            )
            ingested += result.sessions_ingested
            turns += result.turns_ingested
        except Exception as exc:  # defensive: keep corpus ingestion moving
            errors += 1
            conn.execute(
                "insert or replace into sessions(session_id, source_path, format, turn_count, content_hash, status, error_detail) values (?, ?, 'hermes_state_db', 0, ?, 'error', ?)",
                (sid, str(state_db_path), content_hash, str(exc)),
            )
            conn.commit()
    source.close()
    return IngestResult(sessions_ingested=ingested, turns_ingested=turns, errors=errors)
