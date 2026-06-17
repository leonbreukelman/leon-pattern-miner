from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .llm import chat_json
from .sensitivity import mask_sensitive, sensitivity_for_text

DEFAULT_CIE_EXTRACTOR_VERSION = "cie-v1-qwen3.6-opus-fewshot-20260612"
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MAX_WINDOW_TOKENS = 3500
DEFAULT_OVERLAP_TOKENS = 600
DEFAULT_MAX_PROMPT_TOKENS = 7600
DEFAULT_MAX_TURN_CHARS = 2400

CODEBOOK_PATH = Path(__file__).with_name("cie_codebook.json")

SOURCE_RELIABILITY = {"A", "B", "C", "D", "E", "F"}
UNITS = {"turn", "exchange", "arc", "session"}
CONFIDENCE = {"low", "medium", "high"}

FAMILY_ALIASES = {
    "authorization": "authorization_limit",
    "correction": "correction_preference",
    "verification": "verification_review",
    "failure": "verification_review",
    "methodology": "methodology_workflow",
}

FAMILY_PATTERNS = {
    "correction_preference": re.compile(
        r"\b(wrong|not what|instead|prefer|remember|concise|jargon|context window|do not|don't|stop asking|i want|i need)\b",
        re.I,
    ),
    "authorization_limit": re.compile(
        r"\b(do not|don't|go ahead|you can|do it|approval|authorize|permission|draft|send|post|github|reboot|blocked|unblock)\b",
        re.I,
    ),
    "verification_review": re.compile(
        r"\b(verify|test|tests|review|dogfood|smoke|proof|evidence|failed|failure|error|traceback|timeout|REQUEST_CHANGES|rerun)\b",
        re.I,
    ),
    "methodology_workflow": re.compile(
        r"\b(plan|ticket|issue|PR|merge|commit|branch|kanban|weighted|workflow|phase|roadmap|artifact-driven|subagent|pilot|gold set)\b",
        re.I,
    ),
    "model_routing": re.compile(
        r"\b(qwen|opus|fable|grok|claude|sonnet|local model|4090|llama|thinking|no_think|model routing|ask fable|use fable|use opus)\b",
        re.I,
    ),
    "outcome_attribution": re.compile(
        r"\b(redo|redid|rework|wrong target|ambiguous|unclear|had to|caused|landed|shipped|merged|delivered|failed|didn'?t work|start over)\b",
        re.I,
    ),
}

MODEL_ROUTE_RE = re.compile(
    r"\b(qwen|opus|fable|grok|claude|sonnet|gemini|copilot|local model|model_routing|model routing|ask fable|use fable|use opus|delegate(?:d| to)?|subagent)\b",
    re.I,
)

OUTCOME_CODES = {"intent_stated", "delivery_result", "rework_cause"}
DELIVERY_VALUES = {"landed", "partial", "rework", "failed", "unknown"}
CAUSE_VALUES = {"leon_instruction", "agent", "tool", "environment", "none"}


@dataclass(frozen=True)
class CIEWindow:
    window_id: str
    session_id: str
    window_index: int
    turns: list[dict[str, Any]]
    token_estimate: int

    @property
    def turn_start(self) -> int:
        return int(self.turns[0]["idx"])

    @property
    def turn_end(self) -> int:
        return int(self.turns[-1]["idx"])

    @property
    def turn_indices(self) -> list[int]:
        return [int(turn["idx"]) for turn in self.turns]


@dataclass(frozen=True)
class CIEPromptBundle:
    prompt: str
    family: str
    quote_sources: dict[str, str]


@dataclass(frozen=True)
class CIERunSummary:
    sessions_processed: int = 0
    windows_considered: int = 0
    window_runs: int = 0
    records_created: int = 0
    records_rejected: int = 0
    errors: int = 0
    skipped_existing_runs: int = 0
    no_signal_windows: int = 0
    pass_strategy: str = "per_family"
    no_signal_windows_diagnostic: bool = True


def _as_dict(row: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def _clean_text(text: str, *, max_chars: int = DEFAULT_MAX_TURN_CHARS) -> str:
    masked, _ = mask_sensitive(text or "")
    cleaned = re.sub(r"\s+", " ", masked).strip()
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + "…"
    return cleaned


def _turn_prompt_line(turn: Mapping[str, Any], *, max_chars: int = DEFAULT_MAX_TURN_CHARS) -> str:
    tool = f" tool={turn.get('tool_name') or ''}" if turn.get("tool_name") else ""
    return (
        f"turn_id={turn['turn_id']} idx={turn['idx']} actor={turn['actor']}{tool}: "
        f"{_clean_text(str(turn.get('text') or ''), max_chars=max_chars)}"
    )


def _turn_token_estimate(turn: Mapping[str, Any]) -> int:
    return estimate_tokens(_turn_prompt_line(turn))


def load_default_codebook() -> dict[str, Any]:
    return json.loads(CODEBOOK_PATH.read_text(encoding="utf-8"))


def pass_families(codebook: dict[str, Any] | None = None) -> list[str]:
    codebook = codebook or load_default_codebook()
    families = [item["family"] for item in codebook.get("passes", [])]
    for family in ["correction_preference", "authorization_limit", "verification_review", "methodology_workflow"]:
        if family not in families:
            families.append(family)
    return families


def allowed_codes_for_family(family: str, codebook: dict[str, Any] | None = None) -> set[str]:
    codebook = codebook or load_default_codebook()
    family = FAMILY_ALIASES.get(family, family)
    if family == "all":
        return {code["code"] for code in codebook.get("codes", [])}
    for item in codebook.get("passes", []):
        if item.get("family") == family:
            return set(item.get("codes", []))
    return {code["code"] for code in codebook.get("codes", []) if code.get("family") == family}


def build_session_windows(
    turns: Iterable[Mapping[str, Any] | sqlite3.Row],
    *,
    max_window_tokens: int = DEFAULT_MAX_WINDOW_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[CIEWindow]:
    rows = [_as_dict(turn) for turn in turns]
    if not rows:
        return []
    session_id = str(rows[0]["session_id"])
    windows: list[CIEWindow] = []
    start = 0
    n = len(rows)
    while start < n:
        end = start
        token_total = 0
        while end < n:
            turn_tokens = _turn_token_estimate(rows[end])
            if end > start and token_total + turn_tokens > max_window_tokens:
                break
            token_total += turn_tokens
            end += 1
            if token_total >= max_window_tokens:
                break
        if end == start:
            end = start + 1
            token_total = _turn_token_estimate(rows[start])
        window_turns = rows[start:end]
        raw_id = f"{session_id}:{len(windows)}:{window_turns[0]['idx']}:{window_turns[-1]['idx']}"
        window_id = "cie_win_" + hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:16]
        windows.append(
            CIEWindow(
                window_id=window_id,
                session_id=session_id,
                window_index=len(windows),
                turns=window_turns,
                token_estimate=token_total,
            )
        )
        if end >= n:
            break
        overlap_total = 0
        overlap_start = end
        j = end - 1
        while j >= start and overlap_total < overlap_tokens:
            overlap_total += _turn_token_estimate(rows[j])
            overlap_start = j
            j -= 1
        start = max(start + 1, overlap_start)
    return windows


def families_for_window(window: CIEWindow) -> list[str]:
    text = "\n".join(str(turn.get("text") or "") for turn in window.turns)
    families = [family for family, pattern in FAMILY_PATTERNS.items() if pattern.search(text)]
    # Model-routing codes live in the authorization pass. A standalone model signal should therefore
    # trigger authorization_limit, not a dead standalone model_routing pass.
    if "model_routing" in families and "authorization_limit" not in families:
        families.append("authorization_limit")
    ordered = [family for family in pass_families() if family in families]
    return ordered


def families_for_pass_strategy(window: CIEWindow, pass_strategy: str) -> list[str]:
    if pass_strategy == "combined":
        return ["all"]
    if pass_strategy == "per_family":
        return families_for_window(window)
    raise ValueError("pass_strategy must be 'per_family' or 'combined'")


def _code_cards_for_family(family: str, codebook: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = allowed_codes_for_family(family, codebook)
    cards = [code for code in codebook.get("codes", []) if code.get("code") in allowed]
    if family == "all":
        return [
            {
                "code": card.get("code"),
                "family": card.get("family"),
                "definition": card.get("definition"),
                "unit": card.get("unit", []),
                "includes": card.get("includes", [])[:2],
                "excludes": card.get("excludes", [])[:2],
            }
            for card in cards
        ]
    return cards


def _few_shots_for_family(family: str, codebook: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    allowed = allowed_codes_for_family(family, codebook)
    if family == "all":
        limit = 5
    examples = []
    for shot in codebook.get("few_shots", []):
        if shot.get("codebook_code") in allowed or shot.get("source_family") == family:
            examples.append(shot)
    positives = [ex for ex in examples if not ex.get("negative_or_near_miss")]
    negatives = [ex for ex in examples if ex.get("negative_or_near_miss")]
    return (positives[: max(0, limit - 2)] + negatives[:2])[:limit]


def _turn_quote_source(turn: Mapping[str, Any], *, max_chars: int) -> str:
    return _clean_text(str(turn.get("text") or ""), max_chars=max_chars)


def _turn_prompt_line_from_cleaned(turn: Mapping[str, Any], cleaned_text: str) -> str:
    tool = f" tool={turn.get('tool_name') or ''}" if turn.get("tool_name") else ""
    return f"turn_id={turn['turn_id']} idx={turn['idx']} actor={turn['actor']}{tool}: {cleaned_text}"


def _prompt_lines_with_turn_chars(
    lines: list[str],
    window: CIEWindow,
    *,
    max_chars: int,
) -> tuple[list[str], dict[str, str]]:
    quote_sources: dict[str, str] = {}
    out = list(lines)
    for turn in window.turns:
        cleaned = _turn_quote_source(turn, max_chars=max_chars)
        quote_sources[str(turn["turn_id"])] = cleaned
        out.append(_turn_prompt_line_from_cleaned(turn, cleaned))
    return out, quote_sources


def render_cie_prompt_bundle(
    window: CIEWindow,
    *,
    family: str,
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
    codebook: dict[str, Any] | None = None,
) -> CIEPromptBundle:
    codebook = codebook or load_default_codebook()
    family = FAMILY_ALIASES.get(family, family)
    cards = _code_cards_for_family(family, codebook)
    few_shots = _few_shots_for_family(family, codebook)
    payload_schema = {
        "records": [
            {
                "codebook_code": "one of the allowed codes, or omit record",
                "unit": "turn|exchange|arc|session",
                "statement": "concise evidence-backed claim",
                "actor": "leon|agent|tool|system|unknown",
                "source_reliability": "A|B|C|D|E|F",
                "info_credibility": 1,
                "facets": {},
                "evidence": [{"turn_id": "exact id", "quote": "verbatim substring"}],
                "assumptions": ["explicit assumption"],
                "alternative_interpretations": [
                    {"interpretation": "alternative", "why_less_likely": "reason"}
                ],
                "disconfirming_evidence": [],
                "falsifiers": ["what future evidence would change this"],
                "confidence": "low|medium|high",
                "confidence_basis": "written basis",
                "sensitivity": "internal|personal|secret",
            }
        ]
    }
    instruction_lines = [
        "You are doing CIE v1 candidate discovery for Hermes conversation intelligence.",
        "This is report-only candidate extraction, not memory promotion.",
        "Bias toward recall, but every emitted record must be evidence-backed by exact quotes.",
        f"Extraction family: {family}",
        "Allowed codebook codes for this pass:",
        json.dumps(cards, ensure_ascii=False),
        "Few-shot examples and near-misses from the existing corpus:",
        json.dumps(few_shots, ensure_ascii=False),
        "Rules:",
        "1. Return strict JSON only; no markdown, no reasoning, no <think> blocks.",
        "2. Emit the most material candidates in this window, up to 3 records.",
        "3. If no material candidate exists, return {\"records\":[]}.",
        "4. Each quote must be copied character-for-character from the cited turn_id below.",
        "5. Do not create user preference/authorization records from agent-only evidence.",
        "6. Use source_reliability A only for direct Leon instructions/corrections or system-enforced hard blocks; C for agent self-report; D for tool output.",
        "7. unit must be turn, exchange, arc, or session; cross_session is forbidden here.",
        "8. Include assumptions, alternatives, falsifiers, and confidence_basis for each record.",
        "Output schema:",
        json.dumps(payload_schema, ensure_ascii=False),
        "Window turns:",
    ]
    lines, quote_sources = _prompt_lines_with_turn_chars(
        instruction_lines,
        window,
        max_chars=DEFAULT_MAX_TURN_CHARS,
    )
    prompt = "\n".join(lines)
    if estimate_tokens(prompt) <= max_prompt_tokens:
        return CIEPromptBundle(prompt=prompt, family=family, quote_sources=quote_sources)
    # If the instruction/few-shot block plus full turn text is too large, shrink turn text but never drop turns.
    compact_lines, quote_sources = _prompt_lines_with_turn_chars(
        instruction_lines,
        window,
        max_chars=900,
    )
    prompt = "\n".join(compact_lines)
    if estimate_tokens(prompt) <= max_prompt_tokens:
        return CIEPromptBundle(prompt=prompt, family=family, quote_sources=quote_sources)
    # Final guard: keep all turn ids/actors, but heavily clip contents. This is preferable to skipping.
    minimal_lines, quote_sources = _prompt_lines_with_turn_chars(
        instruction_lines,
        window,
        max_chars=420,
    )
    prompt = "\n".join(minimal_lines)
    return CIEPromptBundle(prompt=prompt, family=family, quote_sources=quote_sources)


def render_cie_prompt(
    window: CIEWindow,
    *,
    family: str,
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
    codebook: dict[str, Any] | None = None,
) -> str:
    return render_cie_prompt_bundle(
        window,
        family=family,
        max_prompt_tokens=max_prompt_tokens,
        codebook=codebook,
    ).prompt


def _normalize_confidence(value: Any) -> str:
    if isinstance(value, str) and value.lower() in CONFIDENCE:
        return value.lower()
    if isinstance(value, (int, float)):
        if value >= 0.75:
            return "high"
        if value >= 0.45:
            return "medium"
    return "low"


def _reject(reason: str, record: Any) -> dict[str, Any]:
    return {"reason": reason, "record": record}


def validate_cie_payload(
    payload: Mapping[str, Any],
    source_turns: Mapping[str, Mapping[str, Any]],
    *,
    family: str,
    codebook: dict[str, Any] | None = None,
    quote_source_texts: Mapping[str, str] | None = None,
    max_records: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    codebook = codebook or load_default_codebook()
    source_turns_by_id = {str(turn_id): source for turn_id, source in source_turns.items()}
    quote_sources_by_id = (
        {str(turn_id): text for turn_id, text in quote_source_texts.items()}
        if quote_source_texts is not None
        else None
    )
    allowed = allowed_codes_for_family(family, codebook)
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    records = payload.get("records")
    if not isinstance(records, list):
        return [], [_reject("records_not_list", payload)]
    for record in records:
        if not isinstance(record, dict):
            rejected.append(_reject("record_not_object", record))
            continue
        code = record.get("codebook_code")
        if code not in allowed:
            rejected.append(_reject("code_not_allowed", record))
            continue
        unit = record.get("unit")
        if unit not in UNITS:
            rejected.append(_reject("unit_not_allowed", record))
            continue
        rel = record.get("source_reliability")
        if rel not in SOURCE_RELIABILITY:
            rejected.append(_reject("source_reliability_not_allowed", record))
            continue
        try:
            cred = int(record.get("info_credibility"))
        except (TypeError, ValueError):
            rejected.append(_reject("info_credibility_not_int", record))
            continue
        if cred < 1 or cred > 6:
            rejected.append(_reject("info_credibility_out_of_range", record))
            continue
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            rejected.append(_reject("missing_evidence", record))
            continue
        quote_ok = True
        actors = set()
        for item in evidence:
            if not isinstance(item, dict):
                quote_ok = False
                rejected.append(_reject("evidence_not_object", record))
                break
            turn_id = item.get("turn_id")
            turn_key = str(turn_id)
            quote = item.get("quote")
            if turn_key not in source_turns_by_id:
                quote_ok = False
                rejected.append(_reject("turn_id_not_found", record))
                break
            source = source_turns_by_id[turn_key]
            actors.add(source.get("actor"))
            quote_source = (
                str(quote_sources_by_id[turn_key])
                if quote_sources_by_id is not None and turn_key in quote_sources_by_id
                else str(source.get("text") or "")
            )
            if not isinstance(quote, str) or not quote or quote not in quote_source:
                quote_ok = False
                rejected.append(_reject("quote_not_found", record))
                break
        if not quote_ok:
            continue
        if code == "model_routing":
            route_text = " ".join(
                [str(record.get("statement") or "")]
                + [str(item.get("quote") or "") for item in evidence if isinstance(item, dict)]
            )
            if not MODEL_ROUTE_RE.search(route_text):
                rejected.append(_reject("model_routing_without_named_route", record))
                continue
        if code in OUTCOME_CODES:
            facets = record.get("facets") or {}
            delivery = facets.get("delivery")
            cause = facets.get("cause")
            if delivery not in DELIVERY_VALUES or cause not in CAUSE_VALUES:
                rejected.append(_reject("outcome_facets_invalid", record))
                continue
            if code == "rework_cause" and (
                delivery not in {"partial", "rework", "failed"}
                or cause not in {"leon_instruction", "agent", "tool", "environment"}
            ):
                rejected.append(_reject("rework_cause_without_real_cause", record))
                continue
            if code == "intent_stated" and (delivery != "unknown" or cause != "none"):
                rejected.append(_reject("outcome_facets_invalid", record))
                continue
            if code == "delivery_result" and delivery in {"landed", "unknown"} and cause != "none":
                rejected.append(_reject("outcome_facets_invalid", record))
                continue
        if rel == "A" and not ({"leon", "system"} & actors):
            rejected.append(_reject("source_reliability_a_without_direct_source", record))
            continue
        clean = dict(record)
        clean["info_credibility"] = cred
        clean["confidence"] = _normalize_confidence(clean.get("confidence"))
        clean.setdefault("facets", {})
        clean.setdefault("assumptions", [])
        clean.setdefault("alternative_interpretations", [])
        clean.setdefault("disconfirming_evidence", [])
        clean.setdefault("falsifiers", [])
        clean.setdefault("confidence_basis", "")
        clean["quote_verified"] = True
        if len(valid) >= max_records:
            rejected.append(_reject("too_many_records", record))
            continue
        valid.append(clean)
    return valid, rejected


def init_cie_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists cie_window_runs(
            window_id text not null,
            session_id text not null,
            extractor_version text not null,
            family text not null,
            turn_start integer not null,
            turn_end integer not null,
            token_estimate integer not null,
            status text not null,
            records_created integer not null default 0,
            records_rejected integer not null default 0,
            prompt_hash text,
            error_detail text,
            processed_at text default (datetime('now')),
            primary key(window_id, extractor_version, family)
        )
        """
    )
    conn.execute(
        """
        create table if not exists cie_records(
            record_id text primary key,
            session_id text not null,
            window_id text not null,
            extractor_version text not null,
            family text not null,
            codebook_code text not null,
            unit text not null,
            statement text not null,
            actor text not null,
            source_reliability text not null,
            info_credibility integer not null,
            confidence text not null,
            confidence_basis text,
            sensitivity text not null,
            evidence_json text not null,
            facets_json text not null,
            assumptions_json text not null,
            alternatives_json text not null,
            disconfirming_json text not null,
            falsifiers_json text not null,
            quote_verified integer not null default 1,
            prompt_hash text,
            created_at text default (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        create table if not exists cie_rejections(
            id integer primary key autoincrement,
            window_id text not null,
            session_id text not null,
            extractor_version text not null,
            family text not null,
            rejection_index integer not null,
            rejection_cause text not null,
            record_json text not null,
            prompt_hash text,
            created_at text default (datetime('now')),
            unique(window_id, extractor_version, family, rejection_index)
        )
        """
    )
    conn.commit()


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _record_id(session_id: str, window_id: str, record: Mapping[str, Any]) -> str:
    evidence_key = json.dumps(record.get("evidence", []), sort_keys=True, ensure_ascii=False)
    raw = "|".join([session_id, window_id, str(record.get("codebook_code")), str(record.get("statement")), evidence_key])
    return "cie_rec_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _insert_window_run(
    conn: sqlite3.Connection,
    window: CIEWindow,
    *,
    extractor_version: str,
    family: str,
    status: str,
    records_created: int = 0,
    records_rejected: int = 0,
    prompt_hash: str | None = None,
    error_detail: str | None = None,
) -> None:
    conn.execute(
        """
        insert into cie_window_runs(
            window_id, session_id, extractor_version, family, turn_start, turn_end, token_estimate,
            status, records_created, records_rejected, prompt_hash, error_detail, processed_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        on conflict(window_id, extractor_version, family) do update set
            status=excluded.status,
            records_created=excluded.records_created,
            records_rejected=excluded.records_rejected,
            prompt_hash=excluded.prompt_hash,
            error_detail=excluded.error_detail,
            processed_at=excluded.processed_at
        """,
        (
            window.window_id,
            window.session_id,
            extractor_version,
            family,
            window.turn_start,
            window.turn_end,
            window.token_estimate,
            status,
            records_created,
            records_rejected,
            prompt_hash,
            error_detail,
        ),
    )


def _insert_records(
    conn: sqlite3.Connection,
    window: CIEWindow,
    records: list[dict[str, Any]],
    *,
    extractor_version: str,
    family: str,
    prompt_hash: str,
) -> int:
    inserted = 0
    for record in records:
        rid = _record_id(window.session_id, window.window_id, record)
        evidence_json = _json(record["evidence"])
        sensitivity = str(record.get("sensitivity") or sensitivity_for_text(record.get("statement", "")))
        cur = conn.execute(
            """
            insert or ignore into cie_records(
                record_id, session_id, window_id, extractor_version, family, codebook_code, unit,
                statement, actor, source_reliability, info_credibility, confidence, confidence_basis,
                sensitivity, evidence_json, facets_json, assumptions_json, alternatives_json,
                disconfirming_json, falsifiers_json, quote_verified, prompt_hash
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                rid,
                window.session_id,
                window.window_id,
                extractor_version,
                family,
                record["codebook_code"],
                record["unit"],
                str(record.get("statement") or ""),
                str(record.get("actor") or "unknown"),
                record["source_reliability"],
                record["info_credibility"],
                record["confidence"],
                str(record.get("confidence_basis") or ""),
                sensitivity,
                evidence_json,
                _json(record.get("facets", {})),
                _json(record.get("assumptions", [])),
                _json(record.get("alternative_interpretations", [])),
                _json(record.get("disconfirming_evidence", [])),
                _json(record.get("falsifiers", [])),
                prompt_hash,
            ),
        )
        inserted += cur.rowcount
    return inserted


def _replace_rejections(
    conn: sqlite3.Connection,
    window: CIEWindow,
    rejected: list[dict[str, Any]],
    *,
    extractor_version: str,
    family: str,
    prompt_hash: str,
) -> None:
    conn.execute(
        """
        delete from cie_rejections
        where window_id=? and extractor_version=? and family=?
        """,
        (window.window_id, extractor_version, family),
    )
    for idx, item in enumerate(rejected):
        cause = str(item.get("reason") or "unknown") if isinstance(item, dict) else "unknown"
        conn.execute(
            """
            insert into cie_rejections(
                window_id, session_id, extractor_version, family, rejection_index,
                rejection_cause, record_json, prompt_hash
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                window.window_id,
                window.session_id,
                extractor_version,
                family,
                idx,
                cause,
                _json(item.get("record") if isinstance(item, dict) else item),
                prompt_hash,
            ),
        )


def errored_cie_window_runs(conn: sqlite3.Connection, *, extractor_version: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select window_id, session_id, extractor_version, family, turn_start, turn_end,
               token_estimate, error_detail, processed_at
        from cie_window_runs
        where extractor_version=? and status='error'
        order by session_id, turn_start, family
        """,
        (extractor_version,),
    ).fetchall()
    return [_as_dict(row) for row in rows]


def _session_rows(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select turn_id, session_id, idx, actor, text, coalesce(tool_name, '') as tool_name
        from turns
        where session_id=?
        order by idx
        """,
        (session_id,),
    ).fetchall()
    return [_as_dict(row) for row in rows]


def _already_processed(
    conn: sqlite3.Connection,
    window_id: str,
    extractor_version: str,
    family: str,
) -> bool:
    row = conn.execute(
        """
        select status from cie_window_runs
        where window_id=? and extractor_version=? and family=? and status in ('processed','no_signal')
        """,
        (window_id, extractor_version, family),
    ).fetchone()
    return row is not None


def run_cie_harness(
    conn: sqlite3.Connection,
    *,
    extractor_version: str = DEFAULT_CIE_EXTRACTOR_VERSION,
    base_url: str = DEFAULT_BASE_URL,
    max_sessions: int | None = None,
    session_ids: list[str] | None = None,
    chat_func: Callable[..., dict[str, Any]] = chat_json,
    max_window_tokens: int = DEFAULT_MAX_WINDOW_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
    timeout: int = 180,
    llm_max_tokens: int = 3072,
    pass_strategy: str = "per_family",
    combined_pass: bool = False,
    resume: bool = True,
) -> CIERunSummary:
    init_cie_tables(conn)
    codebook = load_default_codebook()
    if session_ids is None:
        sql = "select session_id from sessions where status in ('normalized','extracted','verified') order by session_id"
        params: tuple[Any, ...] = ()
        if max_sessions is not None:
            sql += " limit ?"
            params = (max_sessions,)
        session_ids = [row["session_id"] for row in conn.execute(sql, params).fetchall()]
    elif max_sessions is not None:
        session_ids = session_ids[:max_sessions]

    summary = CIERunSummary()
    counters = dict(summary.__dict__)
    effective_run_strategy = "combined" if combined_pass else pass_strategy
    counters["pass_strategy"] = effective_run_strategy
    counters["no_signal_windows_diagnostic"] = effective_run_strategy == "per_family"
    for session_id in session_ids:
        turns = _session_rows(conn, session_id)
        if not turns:
            continue
        counters["sessions_processed"] += 1
        windows = build_session_windows(
            turns,
            max_window_tokens=max_window_tokens,
            overlap_tokens=overlap_tokens,
        )
        counters["windows_considered"] += len(windows)
        for window in windows:
            effective_pass_strategy = "combined" if combined_pass else pass_strategy
            families = families_for_pass_strategy(window, effective_pass_strategy)
            if not families:
                _insert_window_run(
                    conn,
                    window,
                    extractor_version=extractor_version,
                    family="no_signal",
                    status="no_signal",
                )
                counters["no_signal_windows"] += 1
                counters["window_runs"] += 1
                conn.commit()
                continue
            source_turns = {turn["turn_id"]: turn for turn in window.turns}
            for family in families:
                if resume and _already_processed(conn, window.window_id, extractor_version, family):
                    counters["skipped_existing_runs"] += 1
                    continue
                prompt_bundle = render_cie_prompt_bundle(
                    window,
                    family=family,
                    max_prompt_tokens=max_prompt_tokens,
                    codebook=codebook,
                )
                prompt = prompt_bundle.prompt
                phash = _prompt_hash(prompt)
                try:
                    try:
                        response = chat_func(
                            prompt,
                            base_url=base_url,
                            timeout=timeout,
                            max_tokens=llm_max_tokens,
                        )
                    except TypeError:
                        response = chat_func(prompt, base_url=base_url, timeout=timeout)
                    payload = response.get("json", response)
                    valid, rejected = validate_cie_payload(
                        payload,
                        source_turns,
                        family=family,
                        codebook=codebook,
                        quote_source_texts=prompt_bundle.quote_sources,
                    )
                    inserted = _insert_records(
                        conn,
                        window,
                        valid,
                        extractor_version=extractor_version,
                        family=family,
                        prompt_hash=phash,
                    )
                    _replace_rejections(
                        conn,
                        window,
                        rejected,
                        extractor_version=extractor_version,
                        family=family,
                        prompt_hash=phash,
                    )
                    _insert_window_run(
                        conn,
                        window,
                        extractor_version=extractor_version,
                        family=family,
                        status="processed",
                        records_created=inserted,
                        records_rejected=len(rejected),
                        prompt_hash=phash,
                    )
                    counters["records_created"] += inserted
                    counters["records_rejected"] += len(rejected)
                    counters["window_runs"] += 1
                    conn.commit()
                except Exception as exc:  # pragma: no cover - live failure path tested by smoke scripts
                    _insert_window_run(
                        conn,
                        window,
                        extractor_version=extractor_version,
                        family=family,
                        status="error",
                        error_detail=str(exc)[:1000],
                        prompt_hash=phash,
                    )
                    counters["errors"] += 1
                    counters["window_runs"] += 1
                    conn.commit()
    return CIERunSummary(**counters)
