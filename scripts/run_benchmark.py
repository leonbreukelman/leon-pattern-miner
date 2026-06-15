#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leon_pattern_miner.adapters import AdapterConfig, diffusion_cli_preflight, get_adapter
from leon_pattern_miner.benchmark import default_result_dir, load_dataset, run_candidate


def _strip_v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _preflight(base_url: str, timeout: int = 5) -> dict:
    root = _strip_v1(base_url)
    with urllib.request.urlopen(f"{root}/v1/models", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run MinerMark: score a CIE extraction model against frozen Opus gold."
    )
    parser.add_argument("--dataset", default="benchmark/cie-extraction-v0")
    parser.add_argument("--model", required=True, help="Candidate model label / endpoint model id")
    parser.add_argument("--adapter", choices=["openai", "diffusion-cli"], default="openai")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
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

    dataset = load_dataset(args.dataset)
    output_dir = Path(args.output_dir) if args.output_dir else default_result_dir(dataset, args.model)

    cfg = AdapterConfig(
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

    if not args.no_preflight and args.adapter == "openai":
        try:
            models = _preflight(args.base_url)
        except Exception as exc:
            print(f"ERROR: local model endpoint is not reachable at {args.base_url}: {exc}", file=sys.stderr)
            return 2
        ids = [str(item.get("id")) for item in models.get("data", []) if isinstance(item, dict)]
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
        threshold=args.threshold,
        served_model_ids=ids,
    )
    print(json.dumps({"scorecard": str(output_dir / "scorecard.md"), "summary": result["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
