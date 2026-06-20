from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .adapters import AdapterConfig, get_adapter
from .cie import (
    DEFAULT_CIE_EXTRACTOR_VERSION,
    DEFAULT_MAX_PROMPT_TOKENS,
    DEFAULT_MAX_WINDOW_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    CIERunSummary,
    estimate_corpus_prompt_count,
    init_cie_tables,
    run_cie_harness,
)
from .db import get_state, init_db, set_state
from .ingest import ingest_hermes_state_db
from .llm import ProviderCallBudget, planned_provider_call_ceiling
from .provider_runtime import DEFAULT_XAI_BASE_URL, load_env_file, model_ids_from_preflight, preflight_openai_models
from .report import write_findings_report

CURSOR_KEY = "last_processed_session_started_at"


def _float_state(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _provider_usage(
    budget: ProviderCallBudget | None,
    *,
    model: str,
    reasoning_effort: str | None,
    cost_cap_usd: float | None,
) -> dict[str, Any] | None:
    if budget is None:
        return None
    return budget.summary(
        provider="xai",
        model=model,
        reasoning_effort=reasoning_effort,
        cost_cap_usd=cost_cap_usd,
    )


def run_mine_cycle(
    conn: sqlite3.Connection,
    *,
    state_db: str | Path = Path.home() / ".hermes" / "state.db",
    limit: int = 20,
    extractor_version: str = DEFAULT_CIE_EXTRACTOR_VERSION,
    model: str = "grok-4.3",
    base_url: str = DEFAULT_XAI_BASE_URL,
    xai_api_key_env: str = "XAI_API_KEY",
    xai_reasoning_effort: str | None = "high",
    pass_strategy: str = "per_family",
    max_window_tokens: int = DEFAULT_MAX_WINDOW_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
    timeout: int = 600,
    llm_max_tokens: int = 4096,
    cost_cap_usd: float | None = 10.0,
    max_model_calls: int | None = None,
    env_file: str | Path = ".env",
    no_preflight: bool = False,
    report_path: str | Path = "runtime/findings-report.md",
    chat_func: Callable[..., dict[str, Any]] | None = None,
    provider_budget: ProviderCallBudget | None = None,
) -> dict[str, Any]:
    """Run one operational mining cycle: ingest newer sessions, extract, dedupe, cursor, report."""
    init_db(conn)
    init_cie_tables(conn)
    load_env_file(env_file)

    cursor_before = _float_state(get_state(conn, CURSOR_KEY))
    ingest = ingest_hermes_state_db(
        conn,
        state_db,
        limit=limit,
        after_started_at=cursor_before,
    )
    session_ids = list(ingest.session_ids)
    planned_prompts = estimate_corpus_prompt_count(
        conn,
        extractor_version=extractor_version,
        session_ids=session_ids,
        max_window_tokens=max_window_tokens,
        overlap_tokens=overlap_tokens,
        pass_strategy=pass_strategy,
        resume=True,
    )
    planned_retry_ceiling = planned_provider_call_ceiling(planned_prompts)
    effective_max_calls = max_model_calls if max_model_calls is not None else planned_retry_ceiling

    served_model_ids: list[str] = []
    budget = provider_budget
    if planned_prompts > 0 and chat_func is None:
        if not no_preflight:
            models = preflight_openai_models(base_url, api_key_env=xai_api_key_env)
            served_model_ids = model_ids_from_preflight(models)
            if served_model_ids and model not in served_model_ids:
                raise RuntimeError(f"model {model!r} is not advertised by endpoint")
        budget = budget or ProviderCallBudget(
            max_calls=effective_max_calls,
            cost_cap_usd=cost_cap_usd,
            cost_estimate_model=model,
        )
        chat_func = get_adapter(
            "xai",
            AdapterConfig(
                provider_budget=budget,
                xai_api_key_env=xai_api_key_env,
                xai_reasoning_effort=xai_reasoning_effort,
            ),
        )
    elif planned_prompts > 0 and budget is None and provider_budget is not None:
        budget = provider_budget

    if planned_prompts > 0 and chat_func is not None:
        summary = run_cie_harness(
            conn,
            extractor_version=extractor_version,
            base_url=base_url,
            session_ids=session_ids,
            chat_func=chat_func,
            max_window_tokens=max_window_tokens,
            overlap_tokens=overlap_tokens,
            max_prompt_tokens=max_prompt_tokens,
            timeout=timeout,
            llm_max_tokens=llm_max_tokens,
            pass_strategy=pass_strategy,
            resume=True,
        )
    else:
        summary = CIERunSummary(pass_strategy=pass_strategy)

    cursor_after = cursor_before
    cursor_advanced = False
    if ingest.max_started_at is not None and ingest.errors == 0 and not summary.budget_exhausted and summary.errors == 0:
        cursor_after = max(cursor_before or float("-inf"), ingest.max_started_at)
        set_state(conn, CURSOR_KEY, str(cursor_after))
        cursor_advanced = True

    report = write_findings_report(conn, report_path)
    provider = _provider_usage(
        budget,
        model=model,
        reasoning_effort=xai_reasoning_effort,
        cost_cap_usd=cost_cap_usd,
    )
    result: dict[str, Any] = {
        "cycle": {
            "state_db": str(state_db),
            "limit": limit,
            "extractor_version": extractor_version,
            "model": model,
            "base_url": base_url,
            "pass_strategy": pass_strategy,
            "planned_prompts": planned_prompts,
            "planned_retry_ceiling": planned_retry_ceiling,
            "max_model_calls": effective_max_calls,
            "cost_cap_usd": cost_cap_usd,
            "served_model_ids": served_model_ids,
        },
        "cursor": {"key": CURSOR_KEY, "before": cursor_before, "after": cursor_after, "advanced": cursor_advanced},
        "ingest": asdict(ingest),
        "summary": asdict(summary),
        "provider_usage": provider,
        "report": str(report),
    }
    return result


def write_schedule_templates(output_dir: str | Path, *, mine_command: str) -> list[Path]:
    """Write disabled scheduler templates; caller/user decides whether to install/enable."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cron = {
        "name": "leon-pattern-miner nightly mine",
        "schedule": "0 3 * * *",
        "enabled": False,
        "prompt": (
            "Run this local command and report only the JSON summary path/output. "
            "Do not create additional cron jobs and do not promote any findings to memory/skills.\n\n"
            f"{mine_command}"
        ),
    }
    cron_path = out / "hermes-cron-mine-nightly.json"
    cron_path.write_text(json.dumps(cron, indent=2), encoding="utf-8")

    service_path = out / "leon-pattern-miner-mine.service"
    service_path.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Run leon-pattern-miner mining cycle",
                "",
                "[Service]",
                "Type=oneshot",
                f"ExecStart=/bin/bash -lc {json.dumps(mine_command)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    timer_path = out / "leon-pattern-miner-mine.timer"
    timer_path.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Nightly leon-pattern-miner mining cycle",
                "",
                "[Timer]",
                "OnCalendar=*-*-* 03:00:00",
                "Persistent=true",
                "Unit=leon-pattern-miner-mine.service",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    readme_path = out / "README.md"
    readme_path.write_text(
        "\n".join(
            [
                "# leon-pattern-miner schedule templates",
                "",
                "These files are generated and ready, but not enabled.",
                "",
                "## Hermes cron",
                "Create manually from `hermes-cron-mine-nightly.json` if Leon chooses Hermes cron.",
                "",
                "## systemd user timer",
                "Copy the `.service` and `.timer` files to `~/.config/systemd/user/`, then run:",
                "",
                "```bash",
                "systemctl --user daemon-reload",
                "systemctl --user enable --now leon-pattern-miner-mine.timer",
                "```",
                "",
                "Do not run those commands until Leon explicitly enables the schedule.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return [cron_path, service_path, timer_path, readme_path]
