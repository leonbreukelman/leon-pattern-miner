from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from .extractors import _actual_tool_failure, _agent_question_anchor, _insert_record, _methodology_sink, ALLOWED_SINKS
from .llm import chat_json, health
from .sensitivity import mask_sensitive

DEFAULT_LLM_EXTRACTOR_VERSION = "local-qwen3.6-35b-a3b-ud-q4km-c8192-v1"

ALLOWED_PATTERN_TYPES = {
    "steering": {
        "recurring_question",
        "clarification_qa",
        "correction",
        "preference",
        "authorization_grant",
        "authorization_limit",
        "escalation_rule",
        "non_escalation_rule",
    },
    "behavior": {
        "clarification_trigger",
        "failure_recovery_arc",
        "wasted_loop",
        "verification_habit",
        "over_asking",
        "under_asking",
        "tool_thrash",
    },
    "methodology": {
        "weighted_intake",
        "strategy_review",
        "plan_spike_build_verify",
        "ticket_contract",
        "dogfooding",
        "review_gate",
        "expected_vs_actual",
        "routing_decision",
        "other_emergent",
    },
}
ALLOWED_STREAMS = set(ALLOWED_PATTERN_TYPES)

ChatFunc = Callable[..., dict[str, Any]]
HealthCheck = Callable[[str], Any]


@dataclass(frozen=True)
class LLMExtractSummary:
    records_created: int = 0
    sessions_processed: int = 0
    errors: int = 0


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))


def _pattern_evidence_ok(stream: str, pattern_type: str, evidence_rows: list[sqlite3.Row], quotes: list[str]) -> bool:
    if stream == "steering" and any(row["actor"] != "leon" for row in evidence_rows):
        return False
    if stream == "methodology" and any(row["actor"] != "agent" for row in evidence_rows):
        return False
    if pattern_type == "clarification_trigger":
        return len(evidence_rows) == 1 and evidence_rows[0]["actor"] == "agent" and _agent_question_anchor(quotes[0])
    if pattern_type in {"failure_recovery_arc", "tool_thrash"}:
        return len(evidence_rows) == 1 and evidence_rows[0]["actor"] == "tool" and _actual_tool_failure(quotes[0])
    return True


def validate_llm_record_payloads(turns: list[sqlite3.Row], payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {row["turn_id"]: row for row in turns}
    valid: list[dict[str, Any]] = []
    for rec in payload.get("records", []):
        if not isinstance(rec, dict):
            continue
        stream = rec.get("stream")
        if stream not in ALLOWED_STREAMS:
            continue
        if rec.get("pattern_type") not in ALLOWED_PATTERN_TYPES[stream]:
            continue
        evidence = rec.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            continue
        ok_evidence = []
        evidence_rows = []
        quotes = []
        for ev in evidence[:1]:
            if not isinstance(ev, dict):
                continue
            turn_id = ev.get("turn_id")
            quote = ev.get("quote")
            row = by_id.get(turn_id)
            if not row or not isinstance(quote, str) or quote not in row["text"]:
                ok_evidence = []
                break
            ok_evidence.append({"turn_id": turn_id, "quote": quote})
            evidence_rows.append(row)
            quotes.append(quote)
        if not ok_evidence or not _pattern_evidence_ok(stream, rec["pattern_type"], evidence_rows, quotes):
            continue
        clean = dict(rec)
        if clean.get("recommended_sink") not in ALLOWED_SINKS:
            clean["recommended_sink"] = "report_only"
        if stream == "methodology":
            quote_text = "\n".join(quotes)
            # Sink decisions must be anchored on the verbatim evidence first;
            # otherwise a generic LLM summary can mask first-person status narration.
            if _methodology_sink(quote_text) == "report_only":
                clean["recommended_sink"] = "report_only"
        clean["evidence"] = ok_evidence
        valid.append(clean)
    return valid


def _mark_session_processed(conn: sqlite3.Connection, *, session_id: str, extractor_version: str, records_created: int) -> None:
    conn.execute(
        """
        insert into llm_session_runs(session_id, extractor_version, status, records_created, processed_at)
        values (?, ?, 'processed', ?, datetime('now'))
        on conflict(session_id, extractor_version) do update set
            status=excluded.status,
            records_created=excluded.records_created,
            processed_at=excluded.processed_at
        """,
        (session_id, extractor_version, records_created),
    )


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"high", "strong"}:
            return 0.8
        if normalized in {"medium", "med", "moderate"}:
            return 0.6
        if normalized in {"low", "weak"}:
            return 0.35
        try:
            return max(0.0, min(1.0, float(normalized)))
        except ValueError:
            return 0.65
    return 0.65


def _candidate_turns(turns: list[sqlite3.Row], *, max_turns: int = 20) -> list[sqlite3.Row]:
    keywords = re.compile(
        r"\b(fable|opus|strategy|pilot|plan|test|implement|verify|review|dogfood|weighted|ticket|ask|asked|question|should|instead|don't|do not|failed|error|traceback|approval|authorize|confirm)\b",
        re.I,
    )
    selected: list[sqlite3.Row] = []
    for idx, row in enumerate(turns):
        text = row["text"] or ""
        if row["actor"] in {"leon", "agent"} and ("?" in text or keywords.search(text)):
            for near in turns[max(0, idx - 1) : min(len(turns), idx + 2)]:
                if near not in selected:
                    selected.append(near)
        elif row["actor"] == "tool" and keywords.search(text):
            if row not in selected:
                selected.append(row)
    if not selected:
        selected = turns[:max_turns]
    return selected[:max_turns]


def _prompt_for_turns(turns: list[sqlite3.Row]) -> str:
    lines = [
        "Extract evidence-backed conversation-mining records for Leon/Hermes autonomy.",
        "Return JSON only: {\"records\":[...]}",
        "Each record must have stream, pattern_type, summary, actor, scope, confidence, recommended_sink, evidence.",
        "Allowed pattern_type values by stream:",
        json.dumps({k: sorted(v) for k, v in ALLOWED_PATTERN_TYPES.items()}),
        "Evidence items must use exact turn_id and a quote copied verbatim from that turn.",
        "Prefer high-precision records over many records. Max 3 records. Keep summaries under 160 chars and quotes short.",
        "",
        "Turn list:",
    ]
    for row in _candidate_turns(turns):
        text, _ = mask_sensitive(row["text"])
        text = text.replace("\n", " ")[:280]
        lines.append(f"- turn_id={row['turn_id']} actor={row['actor']} tool={row['tool_name'] or ''}: {text}")
    return "\n".join(lines)


def planned_llm_sessions(
    conn: sqlite3.Connection,
    *,
    extractor_version: str = DEFAULT_LLM_EXTRACTOR_VERSION,
    max_sessions: int | None = None,
) -> list[str]:
    rows = conn.execute(
        "select session_id from sessions where status in ('normalized','extracted','verified') order by session_id"
    ).fetchall()
    planned: list[str] = []
    for sess in rows:
        if max_sessions is not None and len(planned) >= max_sessions:
            break
        sid = sess["session_id"]
        completed = conn.execute(
            "select 1 from llm_session_runs where session_id=? and extractor_version=? and status='processed'",
            (sid, extractor_version),
        ).fetchone()
        if completed:
            continue
        existing = conn.execute(
            "select count(*) from records where session_id=? and extractor='local_llm' and extractor_version=?",
            (sid, extractor_version),
        ).fetchone()[0]
        if existing:
            continue
        prior_failures = conn.execute(
            """
            select count(*) from errors
            where session_id=?
              and error_class='llm_extract_error'
              and extractor_version=?
            """,
            (sid, extractor_version),
        ).fetchone()[0]
        if prior_failures >= 2:
            continue
        planned.append(sid)
    return planned


def _call_chat(
    chat_func: ChatFunc,
    prompt: str,
    *,
    base_url: str,
    timeout: int,
    max_tokens: int,
    model: str | None,
) -> dict[str, Any]:
    try:
        return chat_func(prompt, base_url=base_url, timeout=timeout, max_tokens=max_tokens, model=model)
    except TypeError:
        return chat_func(prompt, base_url=base_url, timeout=timeout)


def run_llm_extractors(
    conn: sqlite3.Connection,
    *,
    base_url: str = "http://127.0.0.1:8080",
    extractor_version: str = DEFAULT_LLM_EXTRACTOR_VERSION,
    max_sessions: int | None = None,
    timeout: int = 120,
    llm_max_tokens: int = 1536,
    model: str | None = None,
    chat_func: ChatFunc | None = None,
    health_check: HealthCheck | None = None,
) -> LLMExtractSummary:
    health_fn = health_check or health
    h = health_fn(base_url)
    if not h.ok:
        return LLMExtractSummary(errors=1)
    rows = conn.execute(
        "select session_id from sessions where status in ('normalized','extracted','verified') order by session_id"
    ).fetchall()
    created = processed = errors = 0
    attempted = 0
    for sess in rows:
        if max_sessions is not None and attempted >= max_sessions:
            break
        sid = sess["session_id"]
        completed = conn.execute(
            "select 1 from llm_session_runs where session_id=? and extractor_version=? and status='processed'",
            (sid, extractor_version),
        ).fetchone()
        if completed:
            continue
        existing = conn.execute(
            "select count(*) from records where session_id=? and extractor='local_llm' and extractor_version=?",
            (sid, extractor_version),
        ).fetchone()[0]
        if existing:
            _mark_session_processed(conn, session_id=sid, extractor_version=extractor_version, records_created=existing)
            conn.commit()
            continue
        prior_failures = conn.execute(
            """
            select count(*) from errors
            where session_id=?
              and error_class='llm_extract_error'
              and extractor_version=?
            """,
            (sid, extractor_version),
        ).fetchone()[0]
        if prior_failures >= 2:
            continue
        turns = conn.execute("select * from turns where session_id=? order by idx", (sid,)).fetchall()
        attempted += 1
        try:
            payload = _call_chat(
                chat_func or chat_json,
                _prompt_for_turns(turns),
                base_url=base_url,
                timeout=timeout,
                max_tokens=llm_max_tokens,
                model=model,
            )["json"]
            valid = validate_llm_record_payloads(turns, payload)
            session_created = 0
            for rec in valid:
                evidence_rows = [next(row for row in turns if row["turn_id"] == ev["turn_id"]) for ev in rec["evidence"]]
                inserted = _insert_record(
                    conn,
                    session_id=sid,
                    stream=rec["stream"],
                    pattern_type=str(rec.get("pattern_type") or "llm_pattern")[:120],
                    summary=str(rec.get("summary") or "LLM extracted pattern")[:1000],
                    actor=str(rec.get("actor") or "joint")[:30],
                    quote_turns=evidence_rows,
                    extractor="local_llm",
                    extractor_version=extractor_version,
                    scope=str(rec.get("scope") or "session")[:80],
                    confidence=_coerce_confidence(rec.get("confidence")),
                    recommended_sink=str(rec.get("recommended_sink") or "report_only")[:80],
                )
                session_created += inserted
                created += inserted
            _mark_session_processed(conn, session_id=sid, extractor_version=extractor_version, records_created=session_created)
            conn.commit()
            processed += 1
        except Exception as exc:
            errors += 1
            conn.execute(
                """
                insert into errors(session_id, error_class, extractor_version, payload_excerpt)
                values (?, 'llm_extract_error', ?, ?)
                """,
                (sid, extractor_version, str(exc)[:1000]),
            )
            conn.commit()
    return LLMExtractSummary(records_created=created, sessions_processed=processed, errors=errors)
