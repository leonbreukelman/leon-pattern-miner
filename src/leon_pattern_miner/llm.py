from __future__ import annotations

import json
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


def chat_json(prompt: str, *, base_url: str = "http://127.0.0.1:8080", timeout: int = 120) -> dict:
    masked, hits = mask_sensitive(prompt)
    payload = {
        "model": "local-qwen3-32b-q4km",
        "messages": [
            {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
            {"role": "user", "content": masked},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
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
    return {"json": json.loads(content), "masked_hits": len(hits)}
