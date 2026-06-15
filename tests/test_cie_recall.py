"""Tests for the CIE recall gate (Phase C0).

The recall gate compares two extractors over the same sessions and reports
precision/recall/F1 using one extractor's records as the reference (gold).
Matching is by (session_id, codebook_code) with optional quote overlap, so the
scorer is deterministic and offline-testable without any LLM call.
"""
import sqlite3

from leon_pattern_miner.cie_recall import (
    Record,
    score_recall,
    stratified_session_sample,
)


def _r(session, code, quote="q"):
    return Record(session_id=session, codebook_code=code, quote=quote)


def test_perfect_overlap_gives_recall_and_precision_one():
    gold = [_r("s1", "authorization_limit"), _r("s1", "correction_preference")]
    cand = [_r("s1", "authorization_limit"), _r("s1", "correction_preference")]
    m = score_recall(gold=gold, candidate=cand)
    assert m["recall"] == 1.0
    assert m["precision"] == 1.0
    assert m["f1"] == 1.0
    assert m["gold_total"] == 2
    assert m["matched"] == 2


def test_candidate_misses_half_gives_recall_half():
    gold = [_r("s1", "a"), _r("s1", "b")]
    cand = [_r("s1", "a")]  # missed b -> low recall, perfect precision
    m = score_recall(gold=gold, candidate=cand)
    assert m["recall"] == 0.5
    assert m["precision"] == 1.0
    assert round(m["f1"], 3) == 0.667


def test_candidate_overproduces_lowers_precision_not_recall():
    gold = [_r("s1", "a")]
    cand = [_r("s1", "a"), _r("s1", "b"), _r("s1", "c")]  # 2 false positives
    m = score_recall(gold=gold, candidate=cand)
    assert m["recall"] == 1.0
    assert round(m["precision"], 3) == 0.333


def test_matching_is_per_session_not_global():
    # same code but different sessions must NOT match
    gold = [_r("s1", "a")]
    cand = [_r("s2", "a")]
    m = score_recall(gold=gold, candidate=cand)
    assert m["recall"] == 0.0
    assert m["matched"] == 0


def test_empty_gold_returns_defined_zero_division_safe():
    m = score_recall(gold=[], candidate=[_r("s1", "a")])
    assert m["gold_total"] == 0
    assert m["recall"] == 0.0  # nothing to recall
    assert m["precision"] == 0.0  # all candidate are unmatched


def test_stratified_sample_picks_short_medium_long(tmp_path):
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table turns(turn_id text, session_id text, idx int, actor text, text text, tool_name text, char_offset_start int, char_offset_end int)"
    )
    # 9 sessions: 3 short (1-2 turns), 3 medium (5-12), 3 long (40+)
    plan = {
        "short1": 1, "short2": 2, "short3": 2,
        "med1": 6, "med2": 9, "med3": 12,
        "long1": 40, "long2": 80, "long3": 200,
    }
    for sid, n in plan.items():
        for i in range(n):
            conn.execute(
                "insert into turns values (?,?,?,?,?,?,0,0)",
                (f"{sid}:{i}", sid, i, "user", "x", None),
            )
    conn.commit()
    sample = stratified_session_sample(conn, per_bucket=2, seed=7)
    # 2 from each of 3 buckets = 6, all distinct, covering the range
    assert len(sample) == 6
    assert len(set(sample)) == 6
    # at least one short, one medium, one long represented
    assert any(s.startswith("short") for s in sample)
    assert any(s.startswith("med") for s in sample)
    assert any(s.startswith("long") for s in sample)
