from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Any, Callable

from .llm import chat_json, coerce_json_content

ChatFunc = Callable[..., dict[str, Any]]
SubprocessRun = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class AdapterConfig:
    openai_chat_func: ChatFunc = chat_json
    diffusion_bin: str = "llama-diffusion-cli"
    diffusion_model: str | None = None
    diffusion_prompt_mode: str = "file"  # stdin|file|arg
    diffusion_prompt_flag: str = "-f"
    diffusion_ngl: int | None = 99
    diffusion_ctx: int | None = None
    diffusion_steps: int | None = None
    diffusion_extra_args: list[str] = field(default_factory=list)
    require_records_envelope: bool = True
    subprocess_run: SubprocessRun = subprocess.run


def make_openai_adapter(cfg: AdapterConfig) -> ChatFunc:
    return cfg.openai_chat_func


def _resolve_executable(executable: str) -> str:
    if "/" in executable:
        return executable
    return which(executable) or executable


def _model_label(model: str | None, model_path: str) -> str:
    return model or Path(model_path).name


# DiffusionGemma emits its reasoning in an open thought channel and its final
# answer after an answer-channel marker:
#     <|channel>thought ...reasoning... <channel|>ANSWER
# The thought prose frequently contains records-shaped JSON fragments. To avoid
# scoring hallucinated thought-channel text as real benchmark output, extract
# ONLY the answer channel. If a thought channel was opened but no answer channel
# was emitted (the observed out-of-budget failure mode), return empty so JSON
# coercion fails and the window is recorded as an error rather than a false hit.
_THOUGHT_MARKER = "<|channel>"
_ANSWER_MARKER = "<channel|>"


def extract_answer_channel(text: str) -> str:
    if _ANSWER_MARKER in text:
        return text.rsplit(_ANSWER_MARKER, 1)[1]
    if _THOUGHT_MARKER in text:
        return ""
    return text



def _build_diffusion_argv(
    cfg: AdapterConfig,
    *,
    model_path: str,
    max_tokens: int,
) -> list[str]:
    argv = [_resolve_executable(cfg.diffusion_bin), "-m", model_path, "-n", str(max_tokens)]
    if cfg.diffusion_ngl is not None:
        argv.extend(["-ngl", str(cfg.diffusion_ngl)])
    if cfg.diffusion_ctx is not None:
        argv.extend(["-c", str(cfg.diffusion_ctx)])
    if cfg.diffusion_steps is not None:
        argv.extend(["--diffusion-steps", str(cfg.diffusion_steps)])
    argv.extend(cfg.diffusion_extra_args)
    return argv


def make_diffusion_cli_adapter(cfg: AdapterConfig) -> ChatFunc:
    def chat(
        prompt: str,
        *,
        base_url: str = "",
        timeout: int = 300,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> dict[str, Any]:
        del base_url  # subprocess transport, not HTTP
        model_path = cfg.diffusion_model or model
        if not model_path:
            raise ValueError("diffusion-cli adapter requires diffusion_model or model path")
        label = _model_label(model, model_path)
        argv = _build_diffusion_argv(cfg, model_path=model_path, max_tokens=max_tokens)
        run_input: str | None = None
        temp_path: str | None = None
        try:
            if cfg.diffusion_prompt_mode == "stdin":
                run_input = prompt
            elif cfg.diffusion_prompt_mode == "arg":
                argv.extend([cfg.diffusion_prompt_flag, prompt])
            elif cfg.diffusion_prompt_mode == "file":
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
                    temp_path = fh.name
                    fh.write(prompt)
                argv.extend([cfg.diffusion_prompt_flag, temp_path])
            else:
                raise ValueError("diffusion_prompt_mode must be stdin, file, or arg")
            proc = cfg.subprocess_run(
                argv,
                input=run_input,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except FileNotFoundError:
                    pass
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            raise RuntimeError(f"diffusion CLI failed ({proc.returncode}): {stderr[:500]}")
        if not stdout.strip():
            raise RuntimeError("diffusion CLI returned empty stdout")
        answer = extract_answer_channel(stdout)
        if not answer.strip():
            raise RuntimeError(
                "diffusion CLI produced no answer channel (thought-only output); refusing to salvage reasoning text"
            )
        payload = coerce_json_content(answer)
        if cfg.require_records_envelope and not isinstance(payload.get("records"), list):
            raise RuntimeError("diffusion CLI output did not contain a records JSON envelope")
        return {"json": payload, "model_ids": [label]}

    return chat


ADAPTERS: dict[str, Callable[[AdapterConfig], ChatFunc]] = {
    "openai": make_openai_adapter,
    "diffusion-cli": make_diffusion_cli_adapter,
}


def get_adapter(name: str, cfg: AdapterConfig) -> ChatFunc:
    try:
        factory = ADAPTERS[name]
    except KeyError as exc:
        known = ", ".join(ADAPTERS)
        raise ValueError(f"unknown adapter {name!r}; known adapters: {known}") from exc
    return factory(cfg)


def diffusion_cli_preflight(cfg: AdapterConfig) -> list[str]:
    executable = _resolve_executable(cfg.diffusion_bin)
    if not Path(executable).exists() and which(cfg.diffusion_bin) is None:
        raise FileNotFoundError(f"diffusion binary not found: {cfg.diffusion_bin}")
    if not cfg.diffusion_model:
        raise ValueError("--diffusion-model is required for diffusion-cli adapter")
    if not Path(cfg.diffusion_model).exists():
        raise FileNotFoundError(f"diffusion model file not found: {cfg.diffusion_model}")
    return [Path(cfg.diffusion_model).name]
