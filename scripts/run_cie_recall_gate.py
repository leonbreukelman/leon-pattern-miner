#!/usr/bin/env python3
"""Phase C0 recall gate runner (reuses run_cie_harness with an Opus chat_func).

Strategy: instead of re-implementing windowing/validation/insertion, we call the
existing run_cie_harness() with:
  - session_ids = a stratified gold sample
  - chat_func   = an Opus-backed callable matching the chat_json signature
  - extractor_version = 'cie-c0-gold-opus'
This guarantees Opus sees the EXACT same prompt/windows/validator as Qwen.

Then score the existing combined Qwen extractor against Opus-as-gold and report.
Spend: ~1 Opus call per window over the gold sample (bounded, in-scope).
"""
from __future__ import annotations

import json
import subprocess
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leon_pattern_miner.cie import run_cie_harness  # noqa: E402
from leon_pattern_miner.cie_recall import (  # noqa: E402
    score_recall,
    stratified_session_sample,
    records_for_session,
)

DB = "runtime/miner.db"
QWEN_VERSION = "cie-v1-qwen3.6-opus-fewshot-combined-20260612"
GOLD_VERSION = "cie-c0-gold-opus"


def opus_chat(prompt, *, base_url=None, timeout=240, max_tokens=3072):
    """chat_json-compatible: returns {'json': {...}} parsed from Opus output."""
    full = prompt + "\n\nReturn ONLY the strict JSON object described above. No prose, no markdown."
    try:
        proc = subprocess.run(
            ["claude", "-p", full, "--model", "opus", "--allowedTools", "",
             "--max-turns", "1", "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"json": {"records": []}}
    try:
        raw = json.loads(proc.stdout)
    except Exception:
        return {"json": {"records": []}}
    text = (raw.get("result") or "").strip()
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        text = text[s : e + 1]
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "records" in payload:
            return {"json": payload}
    except Exception:
        pass
    return {"json": {"records": []}}


def run():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sample = stratified_session_sample(conn, per_bucket=5, seed=13)
    print(f"gold sample: {len(sample)} sessions", flush=True)

    # clear any prior gold rows so the run is idempotent
    conn.execute("delete from cie_records where extractor_version=?", (GOLD_VERSION,))
    conn.execute("delete from cie_window_runs where extractor_version=?", (GOLD_VERSION,))
    conn.commit()

    summary = run_cie_harness(
        conn,
        extractor_version=GOLD_VERSION,
        session_ids=list(sample),
        chat_func=opus_chat,
        max_window_tokens=3500,
        overlap_tokens=600,
        combined_pass=True,
        timeout=240,
        resume=False,
    )
    print("opus gold run summary:", summary.__dict__, flush=True)

    gold, cand = [], []
    for sid in sample:
        gold += records_for_session(conn, sid, GOLD_VERSION)
        cand += records_for_session(conn, sid, QWEN_VERSION)
    metrics = score_recall(gold=gold, candidate=cand)
    # also session-level coverage: how many gold sessions Qwen found ANY matching code in
    sess_gold = {s for s in sample if records_for_session(conn, s, GOLD_VERSION)}
    sess_cand = {s for s in sample if records_for_session(conn, s, QWEN_VERSION)}

    out = {
        "gold_sample_size": len(sample),
        "gold_extractor": GOLD_VERSION,
        "candidate_extractor": QWEN_VERSION,
        "opus_gold_run": summary.__dict__,
        "metrics": metrics,
        "sessions_with_gold_records": len(sess_gold),
        "sessions_with_qwen_records": len(sess_cand),
    }
    Path("runtime/cie_c0_recall_gate.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({"metrics": metrics,
                      "sessions_with_gold_records": len(sess_gold),
                      "sessions_with_qwen_records": len(sess_cand)}, indent=2))


if __name__ == "__main__":
    run()
