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


ALLOWED_SINKS = {"memory_candidate", "skill_candidate", "profile_candidate", "report_only", "quarantine", "discard"}
DETERMINISTIC_EXTRACTOR_VERSION = "deterministic-v2"


def _record_id(*parts: str) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"rec:{h}"


def _normalize_quote_for_dedupe(quote: str) -> str:
    quote = re.sub(r"\s+", " ", quote.strip()).lower()
    quote = re.sub(r"\bba-m\d+-\d+\b", "ba-m#", quote)
    quote = re.sub(r"\bproc_\w+\b", "proc_#", quote)
    quote = re.sub(r"\b\d+\b", "#", quote)
    if quote.startswith("full verification is green") and "fable" in quote and "review" in quote:
        return "template:full-verification-green-fable-review"
    if quote.startswith("i'm invoking") and "fable" in quote and "review" in quote:
        return "template:invoking-fable-review"
    return quote


def _normalized_evidence_key(evidence: list[dict[str, str]]) -> str:
    if not evidence:
        return ""
    return _normalize_quote_for_dedupe(evidence[0]["quote"])


def _normalized_summary_key(summary: str, evidence_key: str) -> str:
    if evidence_key.startswith("template:"):
        return evidence_key
    return _normalize_quote_for_dedupe(summary)


def _evidence_quote(text: str, *, max_chars: int = 520) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut


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
    extractor_version: str = DETERMINISTIC_EXTRACTOR_VERSION,
    scope: str = "session",
    confidence: float = 0.72,
    recommended_sink: str = "report_only",
) -> bool:
    evidence = []
    text_for_sensitivity = summary
    if recommended_sink not in ALLOWED_SINKS:
        recommended_sink = "report_only"
    for row in quote_turns:
        quote = _evidence_quote(row["text"])
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
    elif sensitivity == "personal" and recommended_sink not in {"report_only", "quarantine"}:
        recommended_sink = "report_only"
    if sensitivity != "internal":
        summary = f"{stream}/{pattern_type} record with suppressed sensitive anchor"
    evidence_key = _normalized_evidence_key(evidence)
    rid = _record_id(
        session_id,
        stream,
        pattern_type,
        actor,
        _normalized_summary_key(summary, evidence_key),
        evidence_key,
    )
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


def _agent_question_anchor(text: str) -> bool:
    cleaned = re.sub(r"https?://\S+", "", text)
    cleaned = re.sub(r"`[^`]*`", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned.endswith("?"):
        return False
    return bool(
        re.match(
            r"(?i)^(should|can|could|would|will|do|does|did|is|are|was|were|what|why|how|which|when|where|may)\b",
            cleaned,
        )
    )


def _has_correction(text: str) -> bool:
    return bool(re.search(r"\b(no|actually|instead|don't|do not|not what|should have|only ask|ask only)\b", text, re.I))


def _brief(text: str, *, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    if max_chars - 1 < len(text) and not text[max_chars - 1].isspace() and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _actual_tool_failure(text: str) -> bool:
    lower = text.lower()
    has_nonzero_exit = re.search(r"exit[_ ]?code['\"]?\s*[:=]\s*[1-9]", lower) is not None
    has_failure_line = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Traceback"):
            return True
        if re.match(r"^(Error|Exception|[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s+", stripped):
            return True
        if re.match(r"(?i)^(failed|failure|fatal|error|command failed)\b", stripped):
            has_failure_line = True
    return has_nonzero_exit and has_failure_line


def _methodology_signal(text: str) -> bool:
    lower = text.lower().strip()
    if lower.startswith(("prompt saved", "done.", "confirmed ", "i'll answer", "i will answer")):
        return False
    keyword_hits = sum(
        1
        for token in ("fable", "opus", "strategy", "pilot", "plan", "test", "implement", "verify", "review", "dogfood", "weighted", "ticket")
        if re.search(rf"\b{re.escape(token)}\b", lower)
    )
    return keyword_hits >= 2


def _methodology_sink(text: str) -> str:
    lower = text.lower().strip().replace("’", "'")
    if lower.startswith(("i'm invoking fable", "dev servers are stopped", "now i'll run", "i'll run", "prompt saved")):
        return "report_only"
    if lower.startswith("full backend") or lower.startswith("full verification is green"):
        return "report_only"
    reusable_method = any(
        marker in lower
        for marker in (
            "controlled sequence",
            "tradeoff",
            "rationale",
            "root cause",
            "regression test first",
            "failing regression test",
            "plan, test, implement",
            "red tests",
        )
    )
    near_term_status = lower.startswith(("i'll ", "i will ", "i'm ", "i am ", "now i'll "))
    if near_term_status and not reusable_method:
        return "report_only"
    if reusable_method:
        return "skill_candidate"
    structural_hits = sum(
        1
        for token in ("plan", "test", "implement", "verify", "review", "dogfood", "weighted", "ticket", "strategy")
        if re.search(rf"\b{re.escape(token)}\b", lower)
    )
    return "skill_candidate" if structural_hits >= 3 else "report_only"


def _increment_cap(caps: dict[tuple[str, str], int], stream: str, pattern_type: str, *, cap: int) -> bool:
    key = (stream, pattern_type)
    if caps.get(key, 0) >= cap:
        return False
    caps[key] = caps.get(key, 0) + 1
    return True


def run_deterministic_extractors(conn: sqlite3.Connection) -> ExtractSummary:
    created = 0
    sessions = conn.execute("select session_id from sessions where status='normalized'").fetchall()
    for sess in sessions:
        sid = sess["session_id"]
        turns = conn.execute("select * from turns where session_id=? order by idx", (sid,)).fetchall()
        caps: dict[tuple[str, str], int] = {}
        for i, row in enumerate(turns):
            text = row["text"]
            lower = text.lower()
            if row["actor"] == "leon" and _agent_question_anchor(text) and _increment_cap(caps, "steering", "clarification_qa", cap=10):
                quote_turns = [row]
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="steering",
                    pattern_type="clarification_qa",
                    summary=f"Leon asks: {_brief(text)}",
                    actor="leon",
                    quote_turns=quote_turns,
                    recommended_sink="memory_candidate",
                )
            if row["actor"] == "leon" and _has_correction(text) and _increment_cap(caps, "steering", "correction", cap=10):
                correction_type = (
                    "authorization_limit"
                    if re.search(r"\b(approval|deploy|destructive|spend|credentials|authorize|ask before|only before)\b", lower)
                    else "correction"
                )
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="steering",
                    pattern_type=correction_type,
                    summary=f"Leon sets boundary: {_brief(text)}",
                    actor="leon",
                    quote_turns=[row],
                    recommended_sink="profile_candidate",
                    confidence=0.82,
                )
            if row["actor"] == "agent" and _agent_question_anchor(text) and _increment_cap(caps, "behavior", "clarification_trigger", cap=8):
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="behavior",
                    pattern_type="clarification_trigger",
                    summary=f"Agent asks: {_brief(text)}",
                    actor="agent",
                    quote_turns=[row],
                    recommended_sink="report_only",
                )
            if row["actor"] == "tool" and row["tool_name"] in {"terminal", "process"} and _actual_tool_failure(text) and _increment_cap(caps, "behavior", "failure_recovery_arc", cap=5):
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="behavior",
                    pattern_type="failure_recovery_arc",
                    summary=f"Terminal/process failure: {_brief(text)}",
                    actor="agent",
                    quote_turns=[row],
                    recommended_sink="skill_candidate",
                )
            if row["actor"] == "agent" and _methodology_signal(text):
                ptype = "strategy_review" if "fable" in lower or "strategy" in lower else "plan_spike_build_verify"
                if not _increment_cap(caps, "methodology", ptype, cap=8):
                    continue
                created += _insert_record(
                    conn,
                    session_id=sid,
                    stream="methodology",
                    pattern_type=ptype,
                    summary=f"{ptype}: {_brief(text)}",
                    actor=row["actor"],
                    quote_turns=[row],
                    recommended_sink=_methodology_sink(text),
                    confidence=0.68,
                )
        conn.execute("update sessions set status='extracted' where session_id=?", (sid,))
    conn.execute(
        """
        update work_items
        set status='completed', finished_at=coalesce(finished_at, datetime('now'))
        where extractor_version in ('deterministic-v1', ?)
          and status in ('pending', 'running')
          and session_id in (select session_id from sessions where status in ('extracted', 'verified'))
        """
        ,
        (DETERMINISTIC_EXTRACTOR_VERSION,),
    )
    conn.commit()
    return ExtractSummary(records_created=created)
