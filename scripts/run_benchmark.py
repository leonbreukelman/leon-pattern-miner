#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leon_pattern_miner.adapters import AdapterConfig, diffusion_cli_preflight, get_adapter
from leon_pattern_miner.benchmark import default_result_dir, estimate_candidate_prompt_count, load_dataset, run_candidate
from leon_pattern_miner.llm import ProviderCallBudget, planned_provider_call_ceiling
from leon_pattern_miner.provider_runtime import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_XAI_BASE_URL,
    load_env_file,
    model_ids_from_preflight,
    preflight_openai_models,
    strip_v1,
    unquote_env_value,
)

_strip_v1 = strip_v1
_preflight = preflight_openai_models
_unquote_env_value = unquote_env_value
_load_env_file = load_env_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run MinerMark: score a CIE extraction model against frozen Opus gold."
    )
    parser.add_argument("--dataset", default="benchmark/cie-extraction-v0")
    parser.add_argument("--model", required=True, help="Candidate model label / endpoint model id")
    parser.add_argument("--adapter", choices=["openai", "diffusion-cli", "xai"], default="openai")
    parser.add_argument("--base-url", default=DEFAULT_LOCAL_BASE_URL)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pass-strategy", choices=["per_family", "combined"], default="per_family")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--trace-dir",
        default=None,
        help="Optional local-only JSONL trace dir with rendered prompts, raw responses, and validation rejections.",
    )
    parser.add_argument("--max-window-tokens", type=int, default=None)
    parser.add_argument("--overlap-tokens", type=int, default=None)
    parser.add_argument("--max-prompt-tokens", type=int, default=7600)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--llm-max-tokens", type=int, default=4096)
    parser.add_argument("--diffusion-bin", default="llama-diffusion-cli")
    parser.add_argument("--diffusion-model", default=None, help="GGUF/model path for --adapter diffusion-cli")
    parser.add_argument("--diffusion-prompt-mode", choices=["stdin", "file", "arg"], default="file")
    parser.add_argument("--diffusion-prompt-flag", default="-f")
    parser.add_argument("--diffusion-ngl", type=int, default=99)
    parser.add_argument("--diffusion-ctx", type=int, default=None)
    parser.add_argument("--diffusion-steps", type=int, default=None)
    parser.add_argument("--diffusion-extra-arg", action="append", default=[])
    parser.add_argument("--xai-api-key-env", default="XAI_API_KEY")
    parser.add_argument("--xai-reasoning-effort", choices=["none", "low", "medium", "high"], default="low")
    parser.add_argument("--max-model-calls", type=int, default=None)
    parser.add_argument("--cost-cap-usd", type=float, default=None)
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Repo-local env file to load without overriding exported env vars.",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip /v1/models reachability check before running live model calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.adapter == "xai" and args.base_url == DEFAULT_LOCAL_BASE_URL:
        args.base_url = DEFAULT_XAI_BASE_URL
    if args.adapter == "xai":
        _load_env_file(args.env_file)

    dataset = load_dataset(args.dataset)
    output_dir = Path(args.output_dir) if args.output_dir else default_result_dir(dataset, args.model)
    provider_budget = None
    if args.adapter == "xai":
        planned_prompts = estimate_candidate_prompt_count(
            dataset,
            runs=args.runs,
            max_window_tokens=args.max_window_tokens,
            overlap_tokens=args.overlap_tokens,
            pass_strategy=args.pass_strategy,
        )
        planned_calls = planned_provider_call_ceiling(planned_prompts)
        if args.max_model_calls is None:
            print(
                f"ERROR: --adapter xai requires --max-model-calls; planned retry-aware ceiling is {planned_calls}",
                file=sys.stderr,
            )
            return 2
        if planned_calls > args.max_model_calls:
            print(
                f"ERROR: planned provider calls exceed --max-model-calls ({planned_calls} > {args.max_model_calls})",
                file=sys.stderr,
            )
            return 2
        provider_budget = ProviderCallBudget(
            max_calls=args.max_model_calls,
            cost_cap_usd=args.cost_cap_usd,
            cost_estimate_model=args.model,
        )

    cfg = AdapterConfig(
        provider_budget=provider_budget,
        xai_api_key_env=args.xai_api_key_env,
        xai_reasoning_effort=args.xai_reasoning_effort,
        diffusion_bin=args.diffusion_bin,
        diffusion_model=args.diffusion_model,
        diffusion_prompt_mode=args.diffusion_prompt_mode,
        diffusion_prompt_flag=args.diffusion_prompt_flag,
        diffusion_ngl=args.diffusion_ngl,
        diffusion_ctx=args.diffusion_ctx,
        diffusion_steps=args.diffusion_steps,
        diffusion_extra_args=args.diffusion_extra_arg,
    )
    chat_func = get_adapter(args.adapter, cfg)

    if not args.no_preflight and args.adapter in {"openai", "xai"}:
        try:
            models = _preflight(
                args.base_url,
                api_key_env=args.xai_api_key_env if args.adapter == "xai" else None,
            )
        except Exception as exc:
            label = "xAI" if args.adapter == "xai" else "local model"
            print(f"ERROR: {label} endpoint is not reachable at {args.base_url}: {exc}", file=sys.stderr)
            return 2
        ids = model_ids_from_preflight(models)
        print("preflight: endpoint OK", ", ".join(ids[:5]) if ids else "(no model ids reported)")
        if ids and args.model not in ids:
            print(
                f"ERROR: --model {args.model!r} is not advertised by endpoint. Served ids: {ids}",
                file=sys.stderr,
            )
            return 2
    elif not args.no_preflight and args.adapter == "diffusion-cli":
        try:
            ids = diffusion_cli_preflight(cfg)
        except Exception as exc:
            print(f"ERROR: diffusion-cli preflight failed: {exc}", file=sys.stderr)
            return 2
        print("preflight: diffusion-cli OK", ", ".join(ids))
    else:
        ids = []

    provider_usage_func = None
    if provider_budget is not None:
        def _provider_usage() -> dict:
            return provider_budget.summary(
                provider="xai",
                model=args.model,
                reasoning_effort=args.xai_reasoning_effort,
                cost_cap_usd=args.cost_cap_usd,
            )

        provider_usage_func = _provider_usage

    result = run_candidate(
        dataset,
        output_dir=output_dir,
        model_name=args.model,
        chat_func=chat_func,
        runs=args.runs,
        base_url=args.base_url,
        max_window_tokens=args.max_window_tokens,
        overlap_tokens=args.overlap_tokens,
        max_prompt_tokens=args.max_prompt_tokens,
        timeout=args.timeout,
        llm_max_tokens=args.llm_max_tokens,
        pass_strategy=args.pass_strategy,
        threshold=args.threshold,
        served_model_ids=ids,
        provider_usage=provider_usage_func,
        trace_dir=args.trace_dir,
    )
    out = {"scorecard": str(output_dir / "scorecard.md"), "summary": result["summary"]}
    if "provider_usage" in result:
        out["provider_usage"] = result["provider_usage"]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
