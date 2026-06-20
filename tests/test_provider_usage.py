import importlib.util
import json
import os
import urllib.request
from pathlib import Path

import pytest

from leon_pattern_miner.adapters import AdapterConfig, make_xai_adapter
from leon_pattern_miner.benchmark import load_dataset, run_candidate
from leon_pattern_miner.llm import (
    OpenAIProviderConfig,
    ProviderCallBudget,
    ProviderBudgetExceeded,
    _normalise_usage,
    chat_json_provider,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _success_payload(content='{"records": []}', *, usage=None):
    payload = {
        "id": "cmpl-test",
        "model": "grok-4.3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_normalise_usage_chat_shape_tracks_cache_reasoning_and_missing_ticks():
    usage = _normalise_usage(
        {
            "prompt_tokens": 32,
            "completion_tokens": 9,
            "total_tokens": 135,
            "prompt_tokens_details": {"cached_tokens": 6},
            "completion_tokens_details": {"reasoning_tokens": 94},
        }
    )

    assert usage == {
        "prompt_tokens": 32,
        "completion_tokens": 9,
        "reasoning_tokens": 94,
        "cached_tokens": 6,
        "total_tokens": 135,
        "cost_in_usd_ticks": None,
        "cost_ticks_present": False,
    }


def test_normalise_usage_responses_shape_tracks_cost_ticks():
    usage = _normalise_usage(
        {
            "input_tokens": 131,
            "input_tokens_details": {"cached_tokens": 128},
            "output_tokens": 624,
            "output_tokens_details": {"reasoning_tokens": 246},
            "total_tokens": 755,
            "cost_in_usd_ticks": 37756000,
        }
    )

    assert usage == {
        "prompt_tokens": 131,
        "completion_tokens": 624,
        "reasoning_tokens": 246,
        "cached_tokens": 128,
        "total_tokens": 755,
        "cost_in_usd_ticks": 37756000,
        "cost_ticks_present": True,
    }


def test_provider_call_budget_reports_exact_partial_unavailable_and_estimated_cost_sources():
    exact = ProviderCallBudget(max_calls=2)
    for ticks in (1000, 2000):
        exact.consume()
        exact.record_usage({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost_in_usd_ticks": ticks, "cost_ticks_present": True})
    exact_summary = exact.summary(provider="xai", model="grok-4.3", reasoning_effort="high", cost_cap_usd=10)
    assert exact_summary["cost"]["cost_source"] == "exact"
    assert exact_summary["cost"]["cost_in_usd_ticks"] == 3000
    assert exact_summary["cost"]["cost_usd"] == pytest.approx(3000 / 10_000_000_000)
    assert exact_summary["calls_priced"] == 2
    assert exact_summary["calls_unpriced"] == 0

    partial = ProviderCallBudget(max_calls=2)
    partial.consume()
    partial.record_usage({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost_in_usd_ticks": 1000, "cost_ticks_present": True})
    partial.consume()
    partial.record_usage({"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "cost_in_usd_ticks": None, "cost_ticks_present": False})
    partial_summary = partial.summary(provider="xai", model="grok-4.3", reasoning_effort="high")
    assert partial_summary["cost"]["cost_source"] == "partial"
    assert partial_summary["calls_priced"] == 1
    assert partial_summary["calls_unpriced"] == 1

    unavailable = ProviderCallBudget(max_calls=1)
    unavailable.consume()
    unavailable_summary = unavailable.summary(provider="xai", model="unknown-model", reasoning_effort="high")
    assert unavailable_summary["cost"]["cost_source"] == "unavailable"
    assert unavailable_summary["cost"]["cost_usd"] is None

    estimated = ProviderCallBudget(max_calls=1)
    estimated.consume()
    estimated.record_usage({"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "reasoning_tokens": 50_000, "total_tokens": 1_150_000, "cost_in_usd_ticks": None, "cost_ticks_present": False})
    estimated_summary = estimated.summary(provider="xai", model="grok-4.3", reasoning_effort="high")
    assert estimated_summary["cost"]["cost_source"] == "estimated"
    assert estimated_summary["cost"]["estimated_cost_usd"] == pytest.approx(1.25 + (150_000 * 2.50 / 1_000_000))


def test_chat_json_provider_records_usage_for_http_success_parse_failed_retry(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    calls = 0

    def fake_urlopen(req, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse(
                _success_payload(
                    "not json",
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "cost_in_usd_ticks": 111,
                    },
                )
            )
        return FakeResponse(
            _success_payload(
                '{"records": []}',
                usage={
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                    "cost_in_usd_ticks": 222,
                },
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    budget = ProviderCallBudget(max_calls=2)
    result = chat_json_provider("prompt", config=OpenAIProviderConfig.xai(), request_budget=budget)

    assert result["json"] == {"records": []}
    assert budget.calls_made == 2
    assert budget.cost_in_usd_ticks == 333
    assert budget.samples[0]["json_parse_ok"] is False
    assert budget.samples[0]["attempt"] == 1
    assert budget.samples[1]["json_parse_ok"] is True


def test_xai_adapter_treats_reasoning_effort_none_as_disabled():
    captured = {}

    def fake_provider(prompt, *, config, **kwargs):
        captured["prompt"] = prompt
        captured["reasoning_effort"] = config.reasoning_effort
        return {"json": {"records": []}}

    chat = make_xai_adapter(AdapterConfig(provider_chat_func=fake_provider, xai_reasoning_effort="none"))

    assert chat("prompt")["json"] == {"records": []}
    assert captured == {"prompt": "prompt", "reasoning_effort": None}


def test_provider_budget_cost_cap_blocks_next_call_after_breach():
    budget = ProviderCallBudget(max_calls=5, cost_cap_usd=0.01)
    budget.consume()
    budget.record_usage({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_in_usd_ticks": 200_000_000, "cost_ticks_present": True})

    summary = budget.summary(provider="xai", model="grok-4.3", reasoning_effort="high", cost_cap_usd=0.01)
    assert summary["cost_cap_breached"] is True
    with pytest.raises(ProviderBudgetExceeded, match="dollar cost cap exhausted"):
        budget.consume()


def test_run_candidate_persists_provider_usage_sidecar_and_scorecard_block(tmp_path):
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
    (root / "sessions" / "s1.json").write_text(
        json.dumps({"session_id": "s1", "bucket": "short", "turns": [{"turn_id": "s1:0", "idx": 0, "actor": "leon", "text": "please verify", "tool_name": ""}]})
    )
    (root / "gold" / "s1.json").write_text(json.dumps({"session_id": "s1", "records": []}))
    (root / "baseline_qwen" / "s1.json").write_text(json.dumps({"session_id": "s1", "records": []}))

    usage = {
        "provider": "xai",
        "model": "grok-4.3",
        "reasoning_effort": "high",
        "max_model_calls": 4,
        "calls_made": 1,
        "calls_priced": 1,
        "calls_unpriced": 0,
        "tokens": {"prompt": 10, "completion": 2, "reasoning": 3, "cached": 1, "total": 15},
        "cost": {"cost_in_usd_ticks": 12345, "cost_usd": 0.0000012345, "cost_source": "exact", "ticks_per_usd": 10_000_000_000},
        "cost_cap_usd": 10.0,
        "cost_cap_breached": False,
        "samples": [],
    }

    result = run_candidate(
        load_dataset(root),
        output_dir=tmp_path / "results",
        model_name="grok-4.3",
        chat_func=lambda prompt, **kwargs: {"json": {"records": []}, "model_ids": ["grok-4.3"]},
        runs=1,
        provider_usage=lambda: usage,
    )

    assert result["provider_usage"] == usage
    assert json.loads((tmp_path / "results" / "provider-usage.json").read_text()) == usage
    assert json.loads((tmp_path / "results" / "scorecard.json").read_text())["provider_usage"] == usage
    scorecard = (tmp_path / "results" / "scorecard.md").read_text()
    assert "## Provider usage / cost" in scorecard
    assert "cost source: exact" in scorecard
    assert "reasoning effort: high" in scorecard


def test_run_candidate_reports_cost_cap_breach_without_second_provider_request(tmp_path):
    root = tmp_path / "dataset"
    (root / "sessions").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "baseline_qwen").mkdir()
    entries = []
    for session_id in ("s1", "s2"):
        entries.append({"session_id": session_id, "file": session_id, "bucket": "short"})
        (root / "sessions" / f"{session_id}.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "bucket": "short",
                    "turns": [
                        {
                            "turn_id": f"{session_id}:0",
                            "idx": 0,
                            "actor": "leon",
                            "text": "synthetic cost-cap exercise only",
                            "tool_name": "",
                        }
                    ],
                }
            )
        )
        (root / "gold" / f"{session_id}.json").write_text(
            json.dumps({"session_id": session_id, "records": []})
        )
        (root / "baseline_qwen" / f"{session_id}.json").write_text(
            json.dumps({"session_id": session_id, "records": []})
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": "cost-cap",
                "window_params": {"max_window_tokens": 1200, "overlap_tokens": 0},
                "totals": {
                    "sessions": 2,
                    "turns": 2,
                    "gold_findings": 0,
                    "qwen_baseline_findings": 0,
                },
                "entries": entries,
            }
        )
    )

    budget = ProviderCallBudget(max_calls=5, cost_cap_usd=0.001, cost_estimate_model="grok-4.3")
    provider_requests = 0

    def capped_chat(_prompt, **_kwargs):
        nonlocal provider_requests
        budget.consume()
        provider_requests += 1
        budget.record_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "reasoning_tokens": 1,
                "cached_tokens": 0,
                "total_tokens": 12,
                "cost_in_usd_ticks": 20_000_000,
                "cost_ticks_present": True,
            },
            json_parse_ok=True,
            attempt=1,
        )
        return {"json": {"records": []}, "model_ids": ["grok-4.3"]}

    result = run_candidate(
        load_dataset(root),
        output_dir=tmp_path / "results",
        model_name="grok-4.3",
        chat_func=capped_chat,
        runs=1,
        pass_strategy="combined",
        provider_usage=lambda: budget.summary(
            provider="xai",
            model="grok-4.3",
            reasoning_effort="high",
            cost_cap_usd=0.001,
        ),
    )

    assert provider_requests == 1
    assert budget.calls_made == 1
    assert result["runs"][0]["transport"]["windows"] == 2
    assert result["runs"][0]["transport"]["error_windows"] == 1
    assert result["provider_usage"]["cost_cap_breached"] is True
    assert result["provider_usage"]["cost"]["cost_source"] == "exact"
    assert result["provider_usage"]["cost"]["cost_usd"] == pytest.approx(0.002)
    provider_usage = json.loads((tmp_path / "results" / "provider-usage.json").read_text())
    assert provider_usage["cost_cap_breached"] is True


def test_run_benchmark_env_loader_loads_dotenv_without_override_or_secret_output(tmp_path, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_benchmark_for_test", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport XAI_API_KEY='from-file-secret'\nOTHER=value=with=equals\n")

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    loaded = mod._load_env_file(env_file)
    assert loaded == ["XAI_API_KEY", "OTHER"]
    assert os.environ["XAI_API_KEY"] == "from-file-secret"
    assert os.environ["OTHER"] == "value=with=equals"
    assert capsys.readouterr().out == ""

    monkeypatch.setenv("XAI_API_KEY", "already-exported")
    loaded = mod._load_env_file(env_file)
    assert "XAI_API_KEY" not in loaded
    assert os.environ["XAI_API_KEY"] == "already-exported"

    assert mod._load_env_file(tmp_path / "missing.env") == []
