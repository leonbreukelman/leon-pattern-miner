import json
import subprocess
import sys
from pathlib import Path

import pytest

from leon_pattern_miner.benchmark import (
    estimate_candidate_prompt_count,
    integrity_report,
    load_dataset,
    run_candidate,
    score_baseline,
    wilson_ci,
)


def test_load_dataset_and_verify_frozen_gold_integrity():
    dataset = load_dataset("benchmark/cie-extraction-v0")
    report = integrity_report(dataset)

    assert dataset.manifest["totals"] == {
        "sessions": 15,
        "turns": 287,
        "gold_findings": 51,
        "qwen_baseline_findings": 29,
    }
    assert len(dataset.sessions) == 15
    assert len(dataset.gold) == 15
    assert len(dataset.baseline) == 15
    assert report["gold_evidence_count"] == 62
    assert report["missing_evidence_turn_ids"] == []


def test_score_baseline_reproduces_known_qwen_v0_result():
    dataset = load_dataset("benchmark/cie-extraction-v0")
    score = score_baseline(dataset)

    assert score["code_level"]["gold_total"] == 51
    assert score["code_level"]["candidate_total"] == 29
    assert score["code_level"]["matched"] == 18
    assert score["code_level"]["recall"] == pytest.approx(0.35294117647058826)
    assert score["code_level"]["agreement_with_opus"] == pytest.approx(0.6206896551724138)
    assert score["quote_strict"]["recall"] == pytest.approx(0.29411764705882354)


def test_wilson_ci_handles_empty_and_surrounds_observed_rate():
    assert wilson_ci(0, 0) == (0.0, 0.0)
    lo, hi = wilson_ci(18, 51)
    assert lo < 18 / 51 < hi
    assert round(hi - lo, 2) >= 0.20


def test_run_candidate_with_fake_chat_writes_predictions_and_scorecard(tmp_path):
    root = tmp_path / "dataset"
    (root / "sessions").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "baseline_qwen").mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mini",
                "window_params": {"max_window_tokens": 1200, "overlap_tokens": 0},
                "totals": {"sessions": 1, "turns": 2, "gold_findings": 1, "qwen_baseline_findings": 0},
                "entries": [{"session_id": "s1", "file": "s1", "bucket": "short"}],
                "known_baseline_result": {"qwen_recall_vs_opus": 0.0},
            }
        )
    )
    turns = [
        {"turn_id": "s1:0", "idx": 0, "actor": "leon", "text": "do not post this anywhere", "tool_name": ""},
        {"turn_id": "s1:1", "idx": 1, "actor": "agent", "text": "acknowledged", "tool_name": ""},
    ]
    (root / "sessions" / "s1.json").write_text(json.dumps({"session_id": "s1", "bucket": "short", "turns": turns}))
    gold_record = {
        "codebook_code": "authorization_limit",
        "unit": "turn",
        "statement": "Leon forbids posting.",
        "actor": "leon",
        "source_reliability": "A",
        "info_credibility": 1,
        "confidence": "high",
        "confidence_basis": "direct instruction",
        "sensitivity": "internal",
        "evidence": [{"turn_id": "s1:0", "quote": "do not post this anywhere"}],
        "facets": {},
        "assumptions": [],
        "alternative_interpretations": [],
        "disconfirming_evidence": [],
        "falsifiers": [],
        "quote_verified": True,
    }
    invalid_record = dict(gold_record)
    invalid_record["codebook_code"] = "methodology_workflow"
    invalid_record["statement"] = "This code is intentionally invalid for the prompted family."
    (root / "gold" / "s1.json").write_text(json.dumps({"session_id": "s1", "extractor": "gold", "records": [gold_record]}))
    (root / "baseline_qwen" / "s1.json").write_text(json.dumps({"session_id": "s1", "extractor": "baseline", "records": []}))

    seen = {}
    seen_prompts = []

    def fake_chat(prompt, *, base_url="http://unused", timeout=1, max_tokens=100, model=None):
        assert "turn_id=s1:0" in prompt
        seen_prompts.append(prompt)
        seen["model"] = model
        seen["base_url"] = base_url
        return {"json": {"records": [gold_record, invalid_record]}, "model_ids": ["fake-model"]}

    dataset = load_dataset(root)
    result = run_candidate(
        dataset,
        output_dir=tmp_path / "results",
        model_name="fake-model",
        chat_func=fake_chat,
        runs=1,
        base_url="http://unused/v1",
        timeout=1,
        llm_max_tokens=100,
        threshold=1.0,
        trace_dir=tmp_path / "traces",
    )

    assert seen["model"] == "fake-model"
    assert seen["base_url"] == "http://unused/v1"
    assert len(seen_prompts) >= 1
    assert any("Extraction family: authorization_limit" in prompt for prompt in seen_prompts)
    assert all("Extraction family: all" not in prompt for prompt in seen_prompts)
    assert estimate_candidate_prompt_count(dataset, runs=1, pass_strategy="per_family") == len(seen_prompts)
    assert result["model"] == "fake-model"
    assert result["served_model_ids"] == ["fake-model"]
    assert result["window_params"]["pass_strategy"] == "per_family"
    assert result["runs"][0]["code_level"]["recall"] == 1.0
    assert result["runs"][0]["code_level"]["agreement_with_opus"] == 1.0
    assert result["recall_pass"] is True
    assert "pass" not in result
    assert (tmp_path / "results" / "run_01" / "predictions" / "s1.json").exists()
    scorecard = (tmp_path / "results" / "scorecard.md").read_text()
    assert "Opus is a strong reference, not ground truth" in scorecard
    assert "agreement-with-Opus" in scorecard
    assert "4090 is not run-to-run deterministic" in scorecard
    assert "transport error-window rate" in scorecard
    trace_path = tmp_path / "traces" / "run_01" / "window-traces.jsonl"
    traces = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert traces
    assert traces[0]["prompt"].startswith("You are doing CIE v1 candidate discovery")
    assert any(
        trace["response_json"]["records"][0]["codebook_code"] == "authorization_limit"
        for trace in traces
    )
    accepted = [record for trace in traces for record in trace["accepted_records"]]
    rejected = [record for trace in traces for record in trace["rejected_records"]]
    assert accepted[0]["codebook_code"] == "authorization_limit"
    assert any(item["reason"] == "code_not_allowed" for item in rejected)


def test_run_candidate_scorecard_surfaces_transport_failures(tmp_path):
    root = tmp_path / "dataset"
    (root / "sessions").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "baseline_qwen").mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mini",
                "window_params": {"max_window_tokens": 1200, "overlap_tokens": 0},
                "totals": {"sessions": 1, "turns": 1, "gold_findings": 0, "qwen_baseline_findings": 0},
                "entries": [{"session_id": "s1", "file": "s1", "bucket": "short"}],
            }
        )
    )
    turns = [{"turn_id": "s1:0", "session_id": "s1", "idx": 0, "actor": "leon", "text": "please verify this", "tool_name": ""}]
    (root / "sessions" / "s1.json").write_text(json.dumps({"session_id": "s1", "bucket": "short", "turns": turns}))
    (root / "gold" / "s1.json").write_text(json.dumps({"session_id": "s1", "extractor": "gold", "records": []}))
    (root / "baseline_qwen" / "s1.json").write_text(json.dumps({"session_id": "s1", "extractor": "baseline", "records": []}))

    def failing_chat(prompt, **kwargs):
        raise RuntimeError("transport failed")

    result = run_candidate(
        load_dataset(root),
        output_dir=tmp_path / "results",
        model_name="broken-model",
        chat_func=failing_chat,
        runs=1,
    )

    assert result["summary"]["transport_error_window_rate"]["mean"] == 1.0
    scorecard = (tmp_path / "results" / "scorecard.md").read_text()
    assert "transport error-window rate mean ± sd: 1.000 ± 0.000" in scorecard


def test_run_candidate_refuses_corrupted_frozen_dataset(tmp_path):
    root = tmp_path / "dataset"
    (root / "sessions").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "baseline_qwen").mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": "broken",
                "window_params": {"max_window_tokens": 1200, "overlap_tokens": 0},
                "totals": {"sessions": 1, "turns": 1, "gold_findings": 1, "qwen_baseline_findings": 0},
                "entries": [{"session_id": "s1", "file": "s1", "bucket": "short"}],
            }
        )
    )
    (root / "sessions" / "s1.json").write_text(
        json.dumps(
            {
                "session_id": "s1",
                "bucket": "short",
                "turns": [{"turn_id": "s1:0", "session_id": "s1", "idx": 0, "actor": "leon", "text": "ok", "tool_name": ""}],
            }
        )
    )
    (root / "gold" / "s1.json").write_text(
        json.dumps(
            {
                "session_id": "s1",
                "extractor": "gold",
                "records": [{"codebook_code": "authorization_limit", "evidence": [{"turn_id": "missing", "quote": "ok"}]}],
            }
        )
    )
    (root / "baseline_qwen" / "s1.json").write_text(json.dumps({"session_id": "s1", "records": []}))

    def fake_chat(prompt, **kwargs):
        raise AssertionError("runner should abort before model calls")

    with pytest.raises(ValueError, match="missing evidence turn_id"):
        run_candidate(
            load_dataset(root),
            output_dir=tmp_path / "results",
            model_name="fake-model",
            chat_func=fake_chat,
            runs=1,
        )


def test_run_benchmark_cli_help_smoke():
    proc = subprocess.run(
        [sys.executable, "scripts/run_benchmark.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "--dataset" in proc.stdout
    assert "--runs" in proc.stdout
    assert "--base-url" in proc.stdout
    assert "--pass-strategy" in proc.stdout
    assert "xai" in proc.stdout
