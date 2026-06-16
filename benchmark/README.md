# CIE Extraction Benchmark — dataset `cie-extraction-v0`

This checked-in dataset is a public-safe **synthetic fixture** for exercising the CIE
benchmark loader/scorer/runner. It preserves the v0 benchmark shape and known
baseline metrics, but it does **not** contain raw Leon/Hermes conversation text.

North star: score whether a candidate model can extract useful, quote-grounded
conversation intelligence — not whether a provider endpoint can merely return JSON.

This is the canonical harness/regression fixture for this repo. For real model-quality
claims, use the same CIE benchmark runner against a private/sanitized gold-set dataset.
Do not use the legacy `miner extract --use-llm` session-level extractor to judge model
quality; that path is only provider-smoke scaffolding unless explicitly migrated to CIE
semantics.

## What's here
```
cie-extraction-v0/
  manifest.json            # metadata, sizing, codebook hash, window params, scoring, known baseline
  sessions/<id>.json       # 15 synthetic public fixture sessions — 287 turns
  gold/<id>.json           # 51 synthetic reference findings (expected answer key shape)
  baseline_qwen/<id>.json  # synthetic baseline preserving the known v0 score shape
```
The private conversation-derived v0 data should stay under ignored local paths such as
`runtime/private-benchmark-backup-*/` and must not be pushed to this public repo.

## Sizing (how much data for a good private benchmark) — read before trusting numbers
- **Public fixture: 15 synthetic sessions / 51 reference findings.** This preserves the
  old v0 score shape and tests the harness; it is not evidence about a model's real
  extraction quality.
- **Private v0-scale gold set: 15 conversations / 51 gold findings.** 95% CI on recall
  ≈ **±0.13**. This is a **directional smoke test**: it tells a clearly-better model
  from a clearly-worse one and catches gross regressions. Do NOT trust deltas under
  ~0.13, and treat per-bucket numbers (10–17 findings each) as illustrative only.
- **Reliable headline: ~150 gold findings (~45–60 conversations)** → CI ≈ ±0.07.
- **Per-bucket-credible: ~300–400 gold findings (~80–120 conversations)**, and only
  with paired (same-session) model-vs-model scoring.
- Power comes from **gold findings, not sessions**; long sessions add ~6 each.

## Known baseline shape
- Qwen recall vs Opus: **0.35** (quote-strict 0.29); agreement-with-Opus 0.62.
- Per-bucket recall: short 0.50 / medium 0.40 / long 0.28 (illustrative; wide CIs).
- These numbers are preserved so tests catch scorer/runner regressions. They are not a
  fresh public model-quality result.

## How the benchmark works (canonical harness)
A model is just a `chat_func`. For each frozen session: `build_session_windows` →
`render_cie_prompt_bundle()` using explicit `--pass-strategy per_family|combined`
(default `per_family`, matching the corpus CIE harness) → candidate model (4090
`http://127.0.0.1:8080/v1`, `--adapter xai`, or another registered adapter) →
`validate_cie_payload` against prompt-visible quote sources → score vs `gold/` with
`leon_pattern_miner.cie_recall.score_recall` (code-level + quote-strict). Output:
predictions + a scorecard with Wilson CIs, per-bucket table, delta-vs-baseline, and
PASS/FAIL vs threshold, averaged over `--runs 3` (the 4090 is not run-to-run
deterministic).

## Rebuild / extend the dataset
```bash
# rebuild private raw benchmark data from the DB (idempotent; no model calls)
uv run python scripts/freeze_benchmark_dataset.py
# grow the gold set (costs Opus money — gate on Leon): raise PER_BUCKET, bump NAME to v1
```

Do not commit raw output from `freeze_benchmark_dataset.py` to the public repo without a
separate sanitization/publication review.

## Roadmap for the harness
`docs/plans/cie-extraction-benchmark-roadmap-2026-06-13.md` (Opus-reviewed,
ACCEPT_WITH_CHANGES, all corrections applied — see reports/critic-loop-bench-roadmap.md).
