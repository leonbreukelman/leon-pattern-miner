"""CIE recall gate (Phase C0).

Deterministic, offline-testable scoring of one extractor against another, plus a
stratified session sampler. The "gold" set is whichever extractor's records you
treat as the reference (for the recall gate we use Opus as gold and score the
local Qwen extractor against it).

Matching rule: a candidate record matches a gold record when they share the same
session_id AND the same codebook_code (greedy one-to-one). This is intentionally
code-level, not quote-exact: the recall question is "did the local model surface
the same *finding* on the same conversation", not "did it quote identical bytes".
Quote overlap is available as a stricter optional mode but defaults off so the
gate measures finding-level recall.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class Record:
    session_id: str
    codebook_code: str
    quote: str = ""


def _quote_overlaps(a: str, b: str, min_tokens: int = 4) -> bool:
    """Loose quote overlap: share >= min_tokens consecutive lowercased words."""
    aw = a.lower().split()
    bw_text = " " + " ".join(b.lower().split()) + " "
    for i in range(len(aw) - min_tokens + 1):
        gram = " " + " ".join(aw[i : i + min_tokens]) + " "
        if gram in bw_text:
            return True
    return False


def score_recall(
    *,
    gold: list[Record],
    candidate: list[Record],
    require_quote_overlap: bool = False,
) -> dict:
    """Return recall/precision/f1 of `candidate` measured against `gold`.

    recall    = matched_gold / gold_total      (did candidate find the gold findings?)
    precision = matched_cand / candidate_total  (were candidate findings real?)
    """
    gold_by_key: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for g in gold:
        gold_by_key[(g.session_id, g.codebook_code)].append(g)

    matched_gold = 0
    matched_cand = 0
    used: set[int] = set()  # indices of consumed gold records (one-to-one)

    # index gold records with stable ids for one-to-one consumption
    gold_index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for idx, g in enumerate(gold):
        gold_index[(g.session_id, g.codebook_code)].append(idx)

    for c in candidate:
        key = (c.session_id, c.codebook_code)
        pool = [i for i in gold_index.get(key, []) if i not in used]
        hit = None
        for i in pool:
            if not require_quote_overlap or _quote_overlaps(c.quote, gold[i].quote):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            matched_gold += 1
            matched_cand += 1

    gold_total = len(gold)
    cand_total = len(candidate)
    recall = matched_gold / gold_total if gold_total else 0.0
    precision = matched_cand / cand_total if cand_total else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {
        "gold_total": gold_total,
        "candidate_total": cand_total,
        "matched": matched_gold,
        "recall": recall,
        "precision": precision,
        "f1": f1,
    }


def _bucket_for(turn_count: int) -> str:
    if turn_count <= 3:
        return "short"
    if turn_count <= 20:
        return "medium"
    return "long"


def stratified_session_sample(
    conn: sqlite3.Connection,
    *,
    per_bucket: int = 5,
    seed: int = 13,
) -> list[str]:
    """Pick `per_bucket` sessions from each of short/medium/long turn-count buckets.

    Deterministic given the seed. Returns a flat list of session_ids.
    """
    import random

    rows = conn.execute(
        "select session_id, count(*) n from turns group by session_id"
    ).fetchall()
    buckets: dict[str, list[str]] = defaultdict(list)
    for session_id, n in rows:
        buckets[_bucket_for(int(n))].append(session_id)

    rng = random.Random(seed)
    out: list[str] = []
    for name in ("short", "medium", "long"):
        pool = sorted(buckets.get(name, []))
        rng.shuffle(pool)
        out.extend(pool[:per_bucket])
    return out


def records_for_session(
    conn: sqlite3.Connection, session_id: str, extractor_version: str
) -> list[Record]:
    """Load CIE records for one session+extractor as Record objects (quote = first evidence)."""
    import json

    out: list[Record] = []
    cur = conn.execute(
        "select codebook_code, evidence_json from cie_records where session_id=? and extractor_version=?",
        (session_id, extractor_version),
    )
    for code, evidence_json in cur.fetchall():
        quote = ""
        try:
            ev = json.loads(evidence_json) if evidence_json else []
            if isinstance(ev, list) and ev:
                quote = ev[0].get("quote", "") if isinstance(ev[0], dict) else str(ev[0])
        except Exception:
            quote = ""
        out.append(Record(session_id=session_id, codebook_code=code, quote=quote))
    return out
