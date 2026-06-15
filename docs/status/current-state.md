# Current state — leon-pattern-miner

Date: 2026-06-15

## North star

Extract durable patterns and intelligence from Leon/Hermes conversations: steering, preferences, authorization boundaries, model-routing rules, agent behavior patterns, verification habits, and reusable workflow methods.

The project is not a generic model bakeoff. Model work is only useful when it improves evidence-backed conversation intelligence extraction.

## Canonical path now

Use the CIE/benchmark path for extraction-quality decisions:

1. `src/leon_pattern_miner/cie.py`
   - window conversations with overlap;
   - render CIE prompts with codebook cards, few-shots, near-misses, schema, and quote rules;
   - validate exact quote evidence.
2. `src/leon_pattern_miner/cie_codebook.json`
   - source of truth for codes and examples.
3. `benchmark/cie-extraction-v0/`
   - public-safe scoring fixture preserving the v0 shape: 15 sessions, 287 turns, 51 reference findings.
   - raw conversation-derived benchmark data stays local/ignored because the GitHub repo is public.
4. `scripts/run_benchmark.py`
   - canonical candidate-model runner for quality comparison.

## Legacy path warning

`miner extract --use-llm` uses `src/leon_pattern_miner/llm_extractors.py`, which selects/truncates candidate turns and uses a thin prompt. It is a legacy/pilot/provider-smoke path, not the canonical model-quality path.

Do not use that path to decide whether Grok, Qwen, DiffusionGemma, or any other frontier/local model is good at conversation intelligence extraction. Use it only for narrow provider-mechanics tests, and label results as such.

## Root cause of the 2026-06-15 Grok mishap

The xAI/Grok adapter work added provider mechanics to `miner extract --use-llm`. I then ran none/low/high reasoning tests through that convenient CLI instead of first asking whether the extractor was the right harness.

That sent Grok a regex-selected, truncated candidate-turn sketch instead of CIE windows with codebook/few-shots. The resulting record counts measure the legacy extractor path, not Grok's real usefulness for the north star.

Corrective guardrails now added:

- `AGENTS.md` states the north star and forbids frontier-model quality evaluation through `llm_extractors.py`.
- `README.md` points agents to the canonical CIE/benchmark path first.
- Grok adapter status docs are marked provider-mechanics-only / not production-quality proof.
- Raw Grok smoke/sample reports are marked provider-smoke-only at the top of each report so future agents do not cite their record counts as model-quality evidence.

## Current model/evaluation status

- The public CIE benchmark fixture exists for harness/scorer regression. Actual model-quality claims require a private/sanitized CIE gold-set recall gate.
- Private C0 Qwen-vs-Opus baseline: recall about 0.35, quote-strict about 0.29, agreement-with-Opus about 0.62. The public fixture preserves that score shape for regression only; Opus is a reference, not ground truth.
- DiffusionGemma local runtime exists but did not emit a valid CIE JSON envelope on a real CIE window; do not run/report a full scorecard yet.
- Grok 4.3 provider adapter mechanics are implemented, but the recent 50-session none/low/high reasoning results are legacy-harness results only. They should not be used to judge extraction quality or production readiness.

## Next safe step

Wire xAI/Grok into the CIE benchmark runner path, then run Grok 4.3 against a private/sanitized CIE gold-set dataset with the same CIE prompt/windowing/validator/scorer used for Qwen and DiffusionGemma work. Use the public `benchmark/cie-extraction-v0/` fixture only for harness mechanics/regression.

Required report shape for that run:

- model and reasoning mode;
- exact benchmark command;
- code-level recall and quote-strict recall vs Opus reference;
- agreement-with-Opus;
- per-bucket short/medium/long results;
- cost and latency;
- caveats, including small v0 benchmark size and Opus-reference-not-ground-truth.

Do not run full corpus until this quality gate is done and Leon approves any paid/off-machine prompt spend.

## Historical docs policy

Dated files under `docs/plans/`, `docs/status/`, `docs/model-facts/`, and `docs/prompts/` are retained as audit history. They are not automatically current. When they conflict with this file or `AGENTS.md`, follow this file and `AGENTS.md`.
