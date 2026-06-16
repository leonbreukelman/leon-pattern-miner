import subprocess

import pytest

from leon_pattern_miner.adapters import AdapterConfig, get_adapter, make_diffusion_cli_adapter, make_openai_adapter, make_xai_adapter
from leon_pattern_miner.llm import ProviderCallBudget, coerce_json_content


def test_coerce_json_content_salvages_common_llm_wrappers():
    assert coerce_json_content('{"records": []}') == {"records": []}
    assert coerce_json_content('```json\n{"records": []}\n```') == {"records": []}
    assert coerce_json_content('<think>hidden</think>\nprose {"records": []} tail') == {"records": []}
    assert coerce_json_content('quoted {"records": ["wrong"]} then final {"records": []}') == {"records": []}
    with pytest.raises(ValueError):
        coerce_json_content('no json here')


def test_get_adapter_lists_known_names_on_unknown_adapter():
    with pytest.raises(ValueError, match="openai.*diffusion-cli.*xai"):
        get_adapter("missing", AdapterConfig())


def test_openai_adapter_forwards_runner_kwargs():
    seen = {}

    def fake_chat(prompt, *, base_url, timeout, max_tokens, model):
        seen.update(
            {"prompt": prompt, "base_url": base_url, "timeout": timeout, "max_tokens": max_tokens, "model": model}
        )
        return {"json": {"records": []}}

    chat = make_openai_adapter(AdapterConfig(openai_chat_func=fake_chat))
    assert chat("PROMPT", base_url="http://x", timeout=7, max_tokens=11, model="m") == {"json": {"records": []}}
    assert seen == {"prompt": "PROMPT", "base_url": "http://x", "timeout": 7, "max_tokens": 11, "model": "m"}


def test_xai_adapter_builds_provider_config_and_uses_shared_budget():
    seen = {}
    budget = ProviderCallBudget(max_calls=2)

    def fake_provider_chat(prompt, *, config, timeout, max_tokens, model, request_budget):
        seen.update(
            {
                "prompt": prompt,
                "provider": config.provider_name,
                "base_url": config.base_url,
                "api_key_env": config.api_key_env,
                "reasoning_effort": config.reasoning_effort,
                "timeout": timeout,
                "max_tokens": max_tokens,
                "model": model,
                "budget": request_budget,
            }
        )
        return {"json": {"records": []}, "model_ids": [model]}

    chat = make_xai_adapter(
        AdapterConfig(
            provider_chat_func=fake_provider_chat,
            provider_budget=budget,
            xai_api_key_env="TEST_XAI_KEY",
            xai_reasoning_effort="high",
        )
    )

    result = chat("PROMPT", base_url="https://api.x.ai/v1", timeout=7, max_tokens=11, model="grok-4.3")

    assert result == {"json": {"records": []}, "model_ids": ["grok-4.3"]}
    assert seen == {
        "prompt": "PROMPT",
        "provider": "xai",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "TEST_XAI_KEY",
        "reasoning_effort": "high",
        "timeout": 7,
        "max_tokens": 11,
        "model": "grok-4.3",
        "budget": budget,
    }


def test_xai_adapter_default_base_url_uses_versioned_api_root():
    seen = {}

    def fake_provider_chat(prompt, *, config, timeout, max_tokens, model, request_budget):
        seen["base_url"] = config.base_url
        return {"json": {"records": []}}

    chat = make_xai_adapter(AdapterConfig(provider_chat_func=fake_provider_chat))
    chat("PROMPT", timeout=1, max_tokens=1, model="grok-4.3")

    assert seen["base_url"] == "https://api.x.ai/v1"


def test_diffusion_cli_adapter_uses_noninteractive_file_prompt_and_returns_contract(tmp_path):
    calls = []

    def fake_run(argv, *, input=None, capture_output=True, text=True, timeout=None, shell=False):
        calls.append({"argv": argv, "input": input, "timeout": timeout, "shell": shell})
        assert shell is False
        assert input is None
        assert "PROMPT WITH JSON {\"records\": []}" not in argv
        prompt_path = argv[argv.index("--prompt-file") + 1]
        with open(prompt_path) as fh:
            assert fh.read() == "PROMPT WITH JSON {\"records\": []}"
        return subprocess.CompletedProcess(argv, 0, stdout='noise {"records": []}', stderr="")

    cfg = AdapterConfig(
        diffusion_bin="/bin/diffuse",
        diffusion_model="/models/dg.gguf",
        diffusion_prompt_mode="file",
        diffusion_prompt_flag="--prompt-file",
        diffusion_extra_args=["--fixed", "1"],
        subprocess_run=fake_run,
    )
    chat = make_diffusion_cli_adapter(cfg)
    result = chat(
        "PROMPT WITH JSON {\"records\": []}",
        base_url="ignored",
        timeout=123,
        max_tokens=77,
        model="diffusiongemma-q4",
    )

    assert result == {"json": {"records": []}, "model_ids": ["diffusiongemma-q4"]}
    argv = calls[0]["argv"]
    assert argv[:4] == ["/bin/diffuse", "-m", "/models/dg.gguf", "-n"]
    assert "77" in argv
    assert "--fixed" in argv


def test_diffusion_cli_adapter_raises_on_missing_records_envelope():
    def evidence_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout='{"turn_id": "s1:0", "quote": "x"}', stderr="")

    chat = make_diffusion_cli_adapter(
        AdapterConfig(diffusion_bin="/bin/diffuse", diffusion_model="m.gguf", subprocess_run=evidence_run)
    )

    with pytest.raises(RuntimeError, match="records JSON envelope"):
        chat("prompt", base_url="ignored", timeout=1, max_tokens=8, model="m")


def test_diffusion_cli_adapter_raises_on_nonzero_or_empty_stdout():
    def bad_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

    chat = make_diffusion_cli_adapter(
        AdapterConfig(diffusion_bin="/bin/diffuse", diffusion_model="m.gguf", subprocess_run=bad_run)
    )

    with pytest.raises(RuntimeError, match="diffusion CLI failed"):
        chat("prompt", base_url="ignored", timeout=1, max_tokens=8, model="m")


def test_extract_answer_channel_picks_answer_not_thought():
    from leon_pattern_miner.adapters import extract_answer_channel

    # thought channel carries a DIFFERENT (hallucinated) records payload than the answer
    raw = (
        '<|channel>thought\n'
        'I think the answer is {"records": [{"codebook_code": "WRONG"}]}\n'
        '<channel|>{"records": []}'
    )
    assert extract_answer_channel(raw).strip() == '{"records": []}'
    # no markers at all -> pass through unchanged
    assert extract_answer_channel('{"records": []}') == '{"records": []}'
    # thought opened but never closed into an answer -> empty (refuse to salvage)
    assert extract_answer_channel('<|channel>thought\nreasoning {"records": [1]} more') == ""


def test_diffusion_cli_adapter_ignores_thought_channel_records():
    # Real-shaped DiffusionGemma stdout: records-shaped JSON in BOTH channels,
    # but with different content. We must return ONLY the answer channel.
    stdout = (
        '\n<|channel>thought\n'
        '*   candidate: {"records": [{"codebook_code": "methodology_workflow"}]}\n'
        '<channel|>{"records": []}\n'
        'total time: 766.88ms\n'
    )

    def channel_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    chat = make_diffusion_cli_adapter(
        AdapterConfig(diffusion_bin="/bin/diffuse", diffusion_model="m.gguf", subprocess_run=channel_run)
    )
    result = chat("prompt", base_url="ignored", timeout=1, max_tokens=8, model="m")
    assert result["json"] == {"records": []}  # answer channel, NOT the thought-channel records


def test_diffusion_cli_adapter_raises_on_thought_only_output():
    # The observed out-of-budget CIE failure mode: thought channel opened,
    # records-shaped fragments inside, but no answer channel emitted.
    stdout = (
        '\n<|channel>thought\n'
        'Record 1: {"records": [{"codebook_code": "verification_review"}]}\n'
        'total time: 43439.60ms\n'
    )

    def thought_only_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    chat = make_diffusion_cli_adapter(
        AdapterConfig(diffusion_bin="/bin/diffuse", diffusion_model="m.gguf", subprocess_run=thought_only_run)
    )
    with pytest.raises(RuntimeError, match="no answer channel"):
        chat("prompt", base_url="ignored", timeout=1, max_tokens=8, model="m")
