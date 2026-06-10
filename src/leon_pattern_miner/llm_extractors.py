from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .extractors import _insert_record
from .llm import chat_json, health
from .sensitivity import mask_sensitive

ALLOWED_STREAMS = {"steering", "behavior", "methodology"}


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


def validate_llm_record_payloads(turns: list[sqlite3.Row], payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {row["turn_id"]: row for row in turns}
    valid: list[dict[str, Any]] = []
    for rec in payload.get("records", []):
        if not isinstance(rec, dict):
            continue
        if rec.get("stream") not in ALLOWED_STREAMS:
            continue
        evidence = rec.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            continue
        ok_evidence = []
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            turn_id = ev.get("turn_id")
            quote = ev.get("quote")
            row = by_id.get(turn_id)
            if not row or not isinstance(quote, str) or quote not in row["text"]:
                ok_evidence = []
                break
            ok_evidence.append({"turn_id": turn_id, "quote": quote})
        if not ok_evidence:
            continue
        clean = dict(rec)
        clean["evidence"] = ok_evidence
        valid.append(clean)
    return valid


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


def _candidate_turns(turns: list[sqlite3.Row], *, max_turns: int = 36) -> list[sqlite3.Row]:
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
        "Allowed streams: steering, behavior, methodology.",
        "Evidence items must use exact turn_id and a quote copied verbatim from that turn.",
        "Prefer high-precision records over many records. Max 8 records.",
        "",
        "Turn list:",
    ]
    for row in _candidate_turns(turns):
        text, _ = mask_sensitive(row["text"])
        text = text.replace("\n", " ")[:420]
        lines.append(f"- turn_id={row['turn_id']} actor={row['actor']} tool={row['tool_name'] or ''}: {text}")
    return "\n".join(lines)


def run_llm_extractors(
    conn: sqlite3.Connection,
    *,
    base_url: str = "http://127.0.0.1:8080",
    extractor_version: str = "local-qwen3-32b-q4km-v1",
    max_sessions: int | None = None,
) -> LLMExtractSummary:
    h = health(base_url)
    if not h.ok:
        return LLMExtractSummary(errors=1)
    rows = conn.execute(
        "select session_id from sessions where status in ('normalized','extracted','verified') order by session_id"
    ).fetchall()
    created = processed = errors = 0
    for sess in rows:
        if max_sessions is not None and processed >= max_sessions:
            break
        sid = sess["session_id"]
        existing = conn.execute(
            "select count(*) from records where session_id=? and extractor_version=?",
            (sid, extractor_version),
        ).fetchone()[0]
        if existing:
            continue
        turns = conn.execute("select * from turns where session_id=? order by idx", (sid,)).fetchall()
        try:
            payload = chat_json(_prompt_for_turns(turns), base_url=base_url)["json"]
            valid = validate_llm_record_payloads(turns, payload)
            for rec in valid:
                evidence_rows = [next(row for row in turns if row["turn_id"] == ev["turn_id"]) for ev in rec["evidence"]]
                created += _insert_record(
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
            processed += 1
        except Exception as exc:
            errors += 1
            conn.execute(
                "insert into errors(session_id, error_class, payload_excerpt) values (?, 'llm_extract_error', ?)",
                (sid, str(exc)[:1000]),
            )
            conn.commit()
    return LLMExtractSummary(records_created=created, sessions_processed=processed, errors=errors)
