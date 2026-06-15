# MinerMark readiness status — 2026-06-13

## Context reconstructed
I mined the prior Hermes conversation for `leon-pattern-miner` / MinerMark. The relevant thread is `20260613_100830_5df6f8` (parent `20260612_194224_18f90f`). It says:

- The Qwen CIE harness was implemented after Opus planned the doctrine and selected few-shot examples from the real corpus.
- The full Hermes corpus CIE run completed later with `runtime/cie_v1_combined_fullrun_summary_latest.json`: 1503 sessions, 2197 window runs, 2620 created records, 1352 rejected records, 95 errored windows.
- The Phase C0 recall gate compared local Qwen against Opus over a 15-session stratified sample.
- The frozen benchmark dataset was exported to `benchmark/cie-extraction-v0/` and the `minermark` Hermes skill was created.
- 2026-06-15 public-repo update: the checked-in `benchmark/cie-extraction-v0/` dataset is a synthetic/public-safe fixture preserving the v0 scoring shape. Raw conversation-derived benchmark data must remain local/ignored unless separately sanitized and approved.

## Current benchmark status

The checked-in benchmark is now a public fixture/regression dataset. It verifies the loader/scorer/runner contract; real model-quality claims require running the same harness against a private/sanitized CIE gold set.
MinerMark is now actually runnable, not just specced.

Built in this pass:
- `src/leon_pattern_miner/benchmark.py`
  - frozen dataset loader
  - integrity enforcement before model calls
  - baseline scorer
  - code-level and quote-strict scoring
  - Wilson 95% CIs
  - multi-run mean ± sd scorecard writer
- `scripts/run_benchmark.py`
  - one-command local OpenAI-compatible runner
  - accepts `http://host:port` or `http://host:port/v1`
  - sends `--model` as the actual OpenAI model id
  - preflights `/v1/models` and fails if `--model` is not advertised
- `tests/test_benchmark.py`
  - frozen dataset integrity
  - baseline reproduction
  - Wilson CI
  - fake-chat end-to-end runner
  - corrupted-dataset abort-before-model-call
  - CLI help smoke
- `tests/test_cie_harness.py`
  - added record-cap regression so spammy model outputs cannot emit unlimited records per window

Skill updated:
- `~/.hermes/skills/mlops/minermark/SKILL.md`

## Frozen v0 dataset verification
Command run:
`uv run python - <<'PY' ...`

Verified:
- sessions: 15
- turns: 287
- gold findings: 51
- Qwen baseline findings: 29
- gold evidence links: 62
- missing evidence turn ids: 0
- gold quote mismatches: 0
- manifest mismatches: 0

Baseline reproduced from frozen files:
- Qwen recall vs Opus: 0.35294117647058826
- Qwen agreement-with-Opus: 0.6206896551724138
- quote-strict recall: 0.29411764705882354

## Test and preflight evidence
Tests:
`uv run pytest -q`

Result:
`56 passed in 0.43s`

Qwen endpoint preflight:
`bash ~/.hermes/skills/software-development/leon-ops-preflight/scripts/capability-preflight.sh qwen`

Result:
`qwen/local: OK (llama-server up on :8080)`

The Docker-managed container `leon-pattern-llama` is running and `/v1/models` advertises:
`unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M`

## Independent review
Initial Opus review:
- artifact: scoped code/test copy under `runtime/minermark_review_scope/`
- output: `runtime/minermark_review_opus.json`
- cost: `$0.624673`
- verdict: `ACCEPT_WITH_CHANGES`
- critical findings fixed:
  1. model provenance was only a label;
  2. threshold PASS was recall-only and mislabeled, plus code-spam could inflate recall;
  3. dataset integrity existed but was not wired into scoring.

Focused Opus re-review:
- output: `runtime/minermark_rereview_opus.json`
- cost: `$0.465453`
- verdict: `ACCEPT`
- `ready_to_run: true`

## Command to run
Use the exact endpoint model id:

```bash
cd $HOME/projects/leon-pattern-miner
uv run python scripts/run_benchmark.py \
  --dataset benchmark/cie-extraction-v0 \
  --model 'unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M' \
  --base-url http://127.0.0.1:8080/v1 \
  --runs 3
```

Expected output root:
`benchmark/results/<model>-<timestamp>/`

Expected key files:
- `scorecard.md`
- `scorecard.json`
- `run_01/predictions/*.json`
- `run_02/predictions/*.json`
- `run_03/predictions/*.json`

## Caveats to preserve when reading the result
- v0 is only 15 conversations / 51 gold findings: directional smoke, not a precise benchmark.
- Opus is a strong reference, not ground truth.
- code-level matching is a lenient upper bound; quote-strict recall is the stricter number.
- The Qwen baseline had an Opus-few-shot home advantage.
- The threshold field is explicitly `recall_pass`; it is a recall-only gate, not overall model correctness.

## Readiness call
Ready to run MinerMark now.
