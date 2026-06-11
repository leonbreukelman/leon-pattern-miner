from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .sensitivity import mask_sensitive


@dataclass(frozen=True)
class LLMHealth:
    ok: bool
    detail: str


def health(base_url: str = "http://127.0.0.1:8080") -> LLMHealth:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/v1/models", timeout=5) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
        return LLMHealth(True, body[:300])
    except Exception as exc:
        return LLMHealth(False, str(exc))


def _parse_json_content(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def chat_json(prompt: str, *, base_url: str = "http://127.0.0.1:8080", timeout: int = 120) -> dict:
    masked, hits = mask_sensitive(prompt)
    payload = {
        "model": "local-qwen3-32b-q4km",
        "messages": [
            {"role": "system", "content": "Return only valid JSON. Do not include markdown, <think> blocks, or reasoning."},
            {"role": "user", "content": "/no_think\n" + masked},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read(500).decode('utf-8', errors='replace')}") from exc
    content = raw["choices"][0]["message"]["content"]
    return {"json": _parse_json_content(content), "masked_hits": len(hits)}
