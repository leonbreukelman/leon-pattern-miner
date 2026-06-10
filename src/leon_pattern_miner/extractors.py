from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass

from .sensitivity import sensitivity_for_text


@dataclass(frozen=True)
class ExtractSummary:
    records_created: int = 0


def _record_id(*parts: str) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"rec:{h}"


def _insert_record(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    stream: str,
    pattern_type: str,
    summary: str,
    actor: str,
    quote_turns: list[sqlite3.Row],
    extractor: str = "deterministic",
    extractor_version: str = "deterministic-v1",
    scope: str = "session",
    confidence: float = 0.72,
    recommended_sink: str = "report_only",
) -> bool:
    evidence = []
    text_for_sensitivity = summary
    for row in quote_turns:
        quote = row["text"].strip()
        if not quote:
            continue
        if quote not in row["text"]:
            raise ValueError("Evidence quote is not verbatim in source turn")
        evidence.append({"turn_id": row["turn_id"], "char_start": 0, "char_end": len(row["text"]), "quote": quote})
        text_for_sensitivity += "\n" + quote
    if not evidence:
        return False
    sensitivity = sensitivity_for_text(text_for_sensitivity)
    if sensitivity == "secret":
        recommended_sink = "quarantine"
    rid = _record_id(session_id, stream, pattern_type, summary, json.dumps(evidence, sort_keys=True))
    cur = conn.execute(
        """
        insert or ignore into records(
            record_id, session_id, stream, pattern_type, summary, evidence_json,
            scope, actor, confidence, sensitivity, verification_status,
            extractor, extractor_version, recommended_sink
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            session_id,
            stream,
            pattern_type,
            summary,
            json.dumps(evidence, ensure_ascii=False),
            scope,
            actor,
            confidence,
            sensitivity,
            "machine_verified",
            extractor,
            extractor_version,
            recommended_sink,
        ),
    )
    return cur.rowcount > 0


def _is_question(text: str) -> bool:
    stripped = text.strip().lower()
    return "?" in stripped or stripped.startswith(("what ", "why ", "how ", "does ", "do ", "is ", "are ", "can ", "should "))


def _has_correction(text: str) -> bool:
    return bool(re.search(r"\b(no|actually|instead|don't|do not|not what|should have|only ask|ask only)\b", text, re.I))


def run_deterministic_extractors(conn: sqlite3.Connection) -> ExtractSummary:
    created = 0
    sessions = conn.execute("select session_id from sessions where status='normalized'").fetchall()
    for sess in sessions:
        sid = sess["session_id"]
        turns = conn.execute("select * from turns where session_id=? order by idx", (sid,)).fetchall()
        for i, row in enumerate(turns):
            text = row["text"]
            lower = text.lower()
            if row["actor"] == "leon" and _is_question(text):
                quote_turns = [row]
                if i + 1 < len(turns) and turns[i + 1]["actor"] == "agent":
                    quote_turns.append(turns[i + 1])
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="steering",
                    pattern_type="clarification_qa",
                    summary="Leon asks a reusable clarification or steering question that can become precedent.",
                    actor="leon",
                    quote_turns=quote_turns,
                    recommended_sink="memory_candidate",
                )
            if row["actor"] == "leon" and _has_correction(text):
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="steering",
                    pattern_type="correction_or_authorization_boundary",
                    summary="Leon corrects agent behavior or defines an authorization/escalation boundary.",
                    actor="leon",
                    quote_turns=[row],
                    recommended_sink="profile_candidate",
                    confidence=0.82,
                )
            if row["actor"] == "agent" and _is_question(text):
                prior = turns[max(0, i - 1) : i + 1]
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="behavior",
                    pattern_type="clarification_trigger",
                    summary="Agent asked a clarification question; preceding context may reveal an avoidable policy gap.",
                    actor="agent",
                    quote_turns=list(prior),
                    recommended_sink="report_only",
                )
            if row["actor"] == "tool" and re.search(r"\b(failed|error|traceback|exception)\b", lower):
                after = [row]
                if i + 1 < len(turns):
                    after.append(turns[i + 1])
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="behavior",
                    pattern_type="tool_failure_recovery",
                    summary="Tool failure followed by agent recovery behavior; candidate for debugging methodology.",
                    actor="agent",
                    quote_turns=after,
                    recommended_sink="skill_candidate",
                )
            if row["actor"] in {"leon", "agent"} and re.search(
                r"\b(fable|opus|strategy|pilot|plan|test|implement|verify|review|dogfood|weighted|ticket)\b",
                lower,
            ):
                ptype = "strategy_review" if "fable" in lower or "strategy" in lower else "plan_test_verify_loop"
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="methodology",
                    pattern_type=ptype,
                    summary="Conversation contains a repeatable project-building methodology signal.",
                    actor=row["actor"],
                    quote_turns=[row],
                    recommended_sink="skill_candidate",
                    confidence=0.68,
                )
        conn.execute("update sessions set status='extracted' where session_id=?", (sid,))
    conn.commit()
    return ExtractSummary(records_created=created)
