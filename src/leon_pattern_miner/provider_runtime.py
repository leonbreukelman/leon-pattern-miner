from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"


def strip_v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def preflight_openai_models(
    base_url: str,
    timeout: int = 5,
    *,
    api_key_env: str | None = None,
) -> dict[str, Any]:
    root = strip_v1(base_url)
    headers = {}
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API key env {api_key_env}")
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{root}/v1/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def model_ids_from_preflight(models: dict[str, Any]) -> list[str]:
    return [str(item.get("id")) for item in models.get("data", []) if isinstance(item, dict)]


def unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: str | Path) -> list[str]:
    """Load simple KEY=VALUE entries without overriding existing environment or printing secrets."""
    env_path = Path(path)
    if not env_path.exists():
        return []
    loaded: list[str] = []
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = unquote_env_value(value)
        loaded.append(key)
    return loaded
