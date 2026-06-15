import io
import json
import urllib.error

import pytest

from leon_pattern_miner.llm import OpenAIProviderConfig, ProviderCallBudget, chat_json_provider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, *args):
        return json.dumps(self.payload).encode("utf-8")


def _success_payload(
    content='{"records": []}',
    *,
    finish_reason="stop",
    model="grok-4.3",
    extra_message=None,
    usage=None,
):
    message = {"content": content}
    if extra_message:
        message.update(extra_message)
    payload = {
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_xai_provider_builds_authenticated_json_request_without_local_llama_fields(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req, timeout, json.loads(req.data.decode("utf-8"))))
        return FakeResponse(_success_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    cfg = OpenAIProviderConfig.xai(model="grok-4.3", base_url="https://api.x.ai/v1")
    result = chat_json_provider("Return records", config=cfg, timeout=17, max_tokens=321)

    assert result["json"] == {"records": []}
    assert result["model_ids"] == ["grok-4.3"]
    req, timeout, body = seen[0]
    assert req.full_url == "https://api.x.ai/v1/chat/completions"
    assert timeout == 17
    assert req.headers["Authorization"] == "Bearer xai-test-secret"
    assert body["model"] == "grok-4.3"
    assert body["max_tokens"] == 321
    assert body["reasoning_effort"] == "low"
    assert "/no_think" not in body["messages"][1]["content"]
    assert "chat_template_kwargs" not in body
    assert body["response_format"] == {"type": "json_object"}


def test_xai_provider_can_disable_reasoning_when_requested(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(_success_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    cfg = OpenAIProviderConfig.xai(reasoning_effort="none")
    chat_json_provider("Return records", config=cfg)

    assert seen[0]["reasoning_effort"] == "none"


def test_xai_base_url_normalization_accepts_root_or_v1(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    urls = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return FakeResponse(_success_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    for base_url in ["https://api.x.ai", "https://api.x.ai/v1", "https://api.x.ai/v1/"]:
        chat_json_provider("{}", config=OpenAIProviderConfig.xai(base_url=base_url))

    assert urls == ["https://api.x.ai/v1/chat/completions"] * 3


def test_local_provider_keeps_llama_no_think_and_has_no_auth(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req, json.loads(req.data.decode("utf-8"))))
        return FakeResponse(_success_payload(model="local-model"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    chat_json_provider("Extract", config=OpenAIProviderConfig.local(model="local-model"))

    req, body = seen[0]
    assert req.full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert "Authorization" not in req.headers
    assert body["messages"][1]["content"].startswith("/no_think\n")
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_remote_provider_masks_prompt_at_transport_boundary(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    bodies = []

    def fake_urlopen(req, timeout=None):
        bodies.append(req.data.decode("utf-8"))
        return FakeResponse(_success_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    chat_json_provider(
        "token=supersecret123 and email leon@example.com",
        config=OpenAIProviderConfig.xai(),
    )

    assert "supersecret123" not in bodies[0]
    assert "leon@example.com" not in bodies[0]
    assert "REDACTED_SECRET" in bodies[0]
    assert "REDACTED_EMAIL" in bodies[0]


def test_missing_api_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    def fake_urlopen(req, timeout=None):  # pragma: no cover - proves no network attempt
        raise AssertionError("network should not be attempted")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="missing API key env XAI_API_KEY"):
        chat_json_provider("prompt", config=OpenAIProviderConfig.xai())


def test_http_error_redacts_key_material(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-real-secret")

    def fake_urlopen(req, timeout=None):
        body = b'{"error":"bad xai-real-secret Authorization: Bearer xai-real-secret"}'
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as excinfo:
        chat_json_provider("prompt", config=OpenAIProviderConfig.xai())

    text = str(excinfo.value)
    assert "xai-real-secret" not in text
    assert "Bearer" not in text
    assert "[REDACTED" in text


@pytest.mark.parametrize(
    "payload, match",
    [
        (_success_payload('{"records": []}', finish_reason="length"), "truncated"),
        (_success_payload(""), "empty visible"),
        (_success_payload(None, extra_message={"reasoning_content": "hidden"}), "empty visible"),
    ],
)
def test_provider_rejects_truncated_empty_or_reasoning_only_content(monkeypatch, payload, match):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")

    def fake_urlopen(req, timeout=None):
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match=match):
        chat_json_provider("prompt", config=OpenAIProviderConfig.xai())


def test_malformed_json_retries_once_when_budget_allows(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    calls = 0

    def fake_urlopen(req, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse(_success_payload("not json"))
        return FakeResponse(_success_payload('{"records": []}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    budget = ProviderCallBudget(max_calls=2)
    result = chat_json_provider("prompt", config=OpenAIProviderConfig.xai(), request_budget=budget)

    assert result["json"] == {"records": []}
    assert calls == 2
    assert budget.calls_made == 2


def test_provider_call_budget_aggregates_response_usage(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")

    def fake_urlopen(req, timeout=None):
        return FakeResponse(
            _success_payload(
                usage={
                    "prompt_tokens": 111,
                    "completion_tokens": 22,
                    "total_tokens": 136,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                }
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    budget = ProviderCallBudget(max_calls=1)
    result = chat_json_provider("prompt", config=OpenAIProviderConfig.xai(), request_budget=budget)

    assert result["usage"] == {
        "prompt_tokens": 111,
        "completion_tokens": 22,
        "reasoning_tokens": 3,
        "total_tokens": 136,
    }
    assert budget.prompt_tokens == 111
    assert budget.completion_tokens == 22
    assert budget.reasoning_tokens == 3
    assert budget.total_tokens == 136


def test_request_budget_blocks_retry_before_second_paid_call(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")
    calls = 0

    def fake_urlopen(req, timeout=None):
        nonlocal calls
        calls += 1
        return FakeResponse(_success_payload("not json"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="model call budget exhausted"):
        chat_json_provider(
            "prompt",
            config=OpenAIProviderConfig.xai(),
            request_budget=ProviderCallBudget(max_calls=1),
        )

    assert calls == 1


def test_429_and_5xx_fail_fast_with_typed_redacted_error(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-secret")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "5"}, io.BytesIO(b"rate"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="LLM HTTP 429"):
        chat_json_provider("prompt", config=OpenAIProviderConfig.xai())
