#!/usr/bin/env python3
"""Freeze the existing Opus-vs-Qwen data into a local benchmark dataset:
(source conversation) + (Opus gold analysis) pairs.

WARNING: this exports raw conversation-derived text from runtime/miner.db. The GitHub
repo is public, so raw output from this script must stay local/ignored unless it has
gone through a separate sanitization/publication review. The checked-in
benchmark/cie-extraction-v0 payload is a synthetic public fixture, not raw transcript
data.

No model calls. Pure export from runtime/miner.db of data already produced in
Phase C0. Output is self-contained JSON under benchmark/<name>/ so the benchmark
never depends on the live DB again.

Layout:
  benchmark/<name>/
    manifest.json                # dataset metadata, sizing, codebook hash, method
    sessions/<session_id>.json   # frozen source conversation (turns)
    gold/<session_id>.json       # frozen Opus gold extraction records
    baseline_qwen/<session_id>.json  # the original 4090 Qwen records (reference baseline)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from leon_pattern_miner.cie_recall import (  # noqa: E402
    stratified_session_sample,
    _bucket_for,
)

DB = "runtime/miner.db"
GOLD_VERSION = "cie-c0-gold-opus"
QWEN_VERSION = "cie-v1-qwen3.6-opus-fewshot-combined-20260612"
NAME = "cie-extraction-v0"
PER_BUCKET = 5
SEED = 13


def _records(conn, sid, version):
    cols = [
        "record_id", "window_id", "family", "codebook_code", "unit", "statement",
        "actor", "source_reliability", "info_credibility", "confidence",
        "confidence_basis", "sensitivity", "evidence_json", "facets_json",
        "assumptions_json", "alternatives_json", "disconfirming_json",
        "falsifiers_json", "quote_verified",
    ]
    rows = conn.execute(
        f"select {','.join(cols)} from cie_records where session_id=? and extractor_version=? order by record_id",
        (sid, version),
    ).fetchall()
    out = []
    for r in rows:
        d = {c: r[i] for i, c in enumerate(cols)}
        for jk in ("evidence_json", "facets_json", "assumptions_json",
                   "alternatives_json", "disconfirming_json", "falsifiers_json"):
            key = jk.replace("_json", "")
            try:
                d[key] = json.loads(d.pop(jk) or "null")
            except Exception:
                d.pop(jk, None)
                d[key] = None
        out.append(d)
    return out


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    root = Path("benchmark") / NAME
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "gold").mkdir(parents=True, exist_ok=True)
    (root / "baseline_qwen").mkdir(parents=True, exist_ok=True)

    sample = stratified_session_sample(conn, per_bucket=PER_BUCKET, seed=SEED)
    codebook_text = Path("src/leon_pattern_miner/cie_codebook.json").read_text()
    codebook_hash = hashlib.sha256(codebook_text.encode()).hexdigest()[:16]

    entries = []
    gold_total = qwen_total = turn_total = 0
    for sid in sample:
        rows = conn.execute(
            "select turn_id, idx, actor, text, tool_name from turns where session_id=? order by idx",
            (sid,),
        ).fetchall()
        turns = [
            {"turn_id": r["turn_id"], "idx": r["idx"], "actor": r["actor"],
             "text": r["text"], "tool_name": r["tool_name"]}
            for r in rows
        ]
        bucket = _bucket_for(len(turns))
        safe = sid.replace(":", "__")
        (root / "sessions" / f"{safe}.json").write_text(
            json.dumps({"session_id": sid, "bucket": bucket, "turns": turns}, indent=2)
        )
        gold = _records(conn, sid, GOLD_VERSION)
        qwen = _records(conn, sid, QWEN_VERSION)
        (root / "gold" / f"{safe}.json").write_text(
            json.dumps({"session_id": sid, "extractor": GOLD_VERSION, "records": gold}, indent=2)
        )
        (root / "baseline_qwen" / f"{safe}.json").write_text(
            json.dumps({"session_id": sid, "extractor": QWEN_VERSION, "records": qwen}, indent=2)
        )
        entries.append({
            "session_id": sid, "file": safe, "bucket": bucket,
            "turns": len(turns), "gold_findings": len(gold), "qwen_findings": len(qwen),
        })
        gold_total += len(gold)
        qwen_total += len(qwen)
        turn_total += len(turns)

    manifest = {
        "name": NAME,
        "version": "0",
        "purpose": "Score any CIE extraction model against an Opus gold answer key over frozen source conversations.",
        "built_from": "runtime/miner.db Phase C0 data (no re-extraction)",
        "gold_extractor": GOLD_VERSION,
        "baseline_extractor": QWEN_VERSION,
        "sampling": {"method": "stratified short/medium/long", "per_bucket": PER_BUCKET, "seed": SEED},
        "codebook_sha256_16": codebook_hash,
        "window_params": {"max_window_tokens": 3500, "overlap_tokens": 600},
        "scoring": {
            "module": "leon_pattern_miner.cie_recall.score_recall",
            "match": "per-session, per codebook_code, greedy one-to-one (code-level)",
            "quote_strict_available": True,
        },
        "totals": {
            "sessions": len(entries), "turns": turn_total,
            "gold_findings": gold_total, "qwen_baseline_findings": qwen_total,
        },
        "buckets": {
            b: sum(1 for e in entries if e["bucket"] == b) for b in ("short", "medium", "long")
        },
        "known_baseline_result": {
            "qwen_recall_vs_opus": 0.35, "qwen_quote_strict_recall": 0.29,
            "qwen_agreement_precision": 0.62,
            "per_bucket_recall": {"short": 0.50, "medium": 0.40, "long": 0.28},
            "caveat": "small sample (51 gold findings); directional, wide CI",
        },
        "entries": entries,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"], indent=2))
    print("frozen to", root)


if __name__ == "__main__":
    main()
