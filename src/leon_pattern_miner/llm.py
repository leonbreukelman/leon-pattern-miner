from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .sensitivity import mask_sensitive

DEFAULT_LLM_MODEL_ID = "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M"


@dataclass(frozen=True)
class LLMHealth:
    ok: bool
    detail: str


@dataclass(frozen=True)
class OpenAIProviderConfig:
    provider_name: str = "local-openai"
    base_url: str = "http://127.0.0.1:8080"
    model: str | None = None
    api_key_env: str | None = None
    send_local_no_think: bool = True
    send_chat_template_kwargs: bool = True
    response_format_json: bool = True
    temperature: float = 0.1
    reasoning_effort: str | None = None

    @classmethod
    def local(
        cls,
        *,
        model: str | None = None,
        base_url: str = "http://127.0.0.1:8080",
    ) -> "OpenAIProviderConfig":
        return cls(
            provider_name="local-openai",
            base_url=base_url,
            model=model,
            api_key_env=None,
            send_local_no_think=True,
            send_chat_template_kwargs=True,
            response_format_json=True,
        )

    @classmethod
    def xai(
        cls,
        *,
        model: str = "grok-4.3",
        base_url: str = "https://api.x.ai",
        api_key_env: str = "XAI_API_KEY",
        reasoning_effort: str | None = "low",
    ) -> "OpenAIProviderConfig":
        return cls(
            provider_name="xai",
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            send_local_no_think=False,
            send_chat_template_kwargs=False,
            response_format_json=True,
            reasoning_effort=reasoning_effort,
        )


@dataclass
class ProviderCallBudget:
    max_calls: int
    calls_made: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def consume(self) -> None:
        if self.calls_made >= self.max_calls:
            raise RuntimeError(
                f"model call budget exhausted before provider request ({self.calls_made}/{self.max_calls})"
            )
        self.calls_made += 1

    def record_usage(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.reasoning_tokens += int(usage.get("reasoning_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)


def _normalise_usage(raw_usage: Any) -> dict[str, int] | None:
    if not isinstance(raw_usage, dict):
        return None
    prompt_tokens = int(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)) or 0)
    completion_tokens = int(raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)) or 0)
    completion_details = raw_usage.get("completion_tokens_details") or raw_usage.get("output_tokens_details") or {}
    reasoning_tokens = 0
    if isinstance(completion_details, dict):
        reasoning_tokens = int(completion_details.get("reasoning_tokens", 0) or 0)
    total_tokens = int(raw_usage.get("total_tokens", 0) or 0)
    if total_tokens == 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens + reasoning_tokens
    if not (prompt_tokens or completion_tokens or reasoning_tokens or total_tokens):
        return None
    if reasoning_tokens == 0 and total_tokens > prompt_tokens + completion_tokens:
        reasoning_tokens = total_tokens - prompt_tokens - completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def _normalise_root_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def _chat_url(base_url: str) -> str:
    return f"{_normalise_root_url(base_url)}/v1/chat/completions"


def _models_url(base_url: str) -> str:
    return f"{_normalise_root_url(base_url)}/v1/models"


def _api_key(config: OpenAIProviderConfig) -> str | None:
    if not config.api_key_env:
        return None
    value = os.environ.get(config.api_key_env)
    if not value:
        raise RuntimeError(f"missing API key env {config.api_key_env}")
    return value


def _redact_error(text: str, *, api_key: str | None = None) -> str:
    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED_API_KEY]")
    redacted = re.sub(r"Authorization:\s*Bearer\s+\S+", "Authorization: [REDACTED_AUTH]", redacted, flags=re.I)
    redacted = re.sub(r"Bearer\s+\S+", "[REDACTED_AUTH]", redacted, flags=re.I)
    redacted, _ = mask_sensitive(redacted)
    return redacted


def health(
    base_url: str = "http://127.0.0.1:8080",
    *,
    api_key_env: str | None = None,
    timeout: int = 5,
) -> LLMHealth:
    try:
        headers = {}
        api_key = None
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if not api_key:
                return LLMHealth(False, f"missing API key env {api_key_env}")
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(_models_url(base_url), headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
        return LLMHealth(True, body[:300])
    except Exception as exc:
        return LLMHealth(False, _redact_error(str(exc), api_key=os.environ.get(api_key_env or "")))


def _parse_json_content(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
    decoder = json.JSONDecoder()
    try:
        parsed = decoder.decode(content)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("JSON content is not an object")
    except json.JSONDecodeError:
        candidates: list[dict] = []
        for match in re.finditer(r"{", content):
            try:
                parsed, _end = decoder.raw_decode(content[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                candidates.append(parsed)
        if candidates:
            return candidates[-1]
        raise


def coerce_json_content(content: str) -> dict:
    return _parse_json_content(content)


def _visible_content(raw: dict[str, Any]) -> tuple[str, str | None]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response missing choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("LLM response choice is not an object")
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise RuntimeError("LLM response truncated (finish_reason=length)")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("LLM response missing assistant message")
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response had empty visible assistant content")
    return content, str(raw.get("model") or "") or None


def chat_json_provider(
    prompt: str,
    *,
    config: OpenAIProviderConfig,
    timeout: int = 120,
    max_tokens: int = 1536,
    model: str | None = None,
    request_budget: ProviderCallBudget | None = None,
) -> dict:
    api_key = _api_key(config)
    last_parse_error: Exception | None = None
    total_masked_hits = 0
    for attempt in range(2):
        retry_suffix = (
            ""
            if attempt == 0
            else '\n\nPrevious response was invalid JSON. Return a compact valid JSON object only; use {"records": []} if no records qualify.'
        )
        masked, hits = mask_sensitive(prompt + retry_suffix)
        total_masked_hits += len(hits)
        user_content = masked
        if config.send_local_no_think:
            user_content = "/no_think\n" + user_content
        payload: dict[str, Any] = {
            "model": model or config.model or os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID),
            "messages": [
                {"role": "system", "content": "Return only valid JSON. Do not include markdown, <think> blocks, or reasoning."},
                {"role": "user", "content": user_content},
            ],
            "temperature": config.temperature,
            "max_tokens": max_tokens,
        }
        if config.response_format_json:
            payload["response_format"] = {"type": "json_object"}
        if config.reasoning_effort is not None:
            payload["reasoning_effort"] = config.reasoning_effort
        if config.send_chat_template_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            _chat_url(config.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            if request_budget is not None:
                request_budget.consume()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {_redact_error(detail, api_key=api_key)}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM transport error: {_redact_error(str(exc), api_key=api_key)}") from exc
        usage = _normalise_usage(raw.get("usage"))
        if request_budget is not None:
            request_budget.record_usage(usage)
        content, served_model = _visible_content(raw)
        try:
            result = {
                "json": _parse_json_content(content),
                "masked_hits": total_masked_hits,
                "model_ids": [served_model or payload["model"]],
            }
            if usage is not None:
                result["usage"] = usage
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            last_parse_error = exc
            if attempt == 0:
                continue
            raise
    if last_parse_error is not None:
        raise last_parse_error
    raise RuntimeError("LLM JSON parsing failed without an error")


def chat_json(
    prompt: str,
    *,
    base_url: str = "http://127.0.0.1:8080",
    timeout: int = 120,
    max_tokens: int = 1536,
    model: str | None = None,
) -> dict:
    result = chat_json_provider(
        prompt,
        config=OpenAIProviderConfig.local(model=model, base_url=base_url),
        timeout=timeout,
        max_tokens=max_tokens,
        model=model,
    )
    # Preserve the historical local chat_json contract used by existing tests/callers.
    result.pop("model_ids", None)
    return result
