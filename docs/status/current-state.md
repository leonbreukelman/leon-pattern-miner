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
   - validate exact quote evidence against the same prompt-visible cleaned/masked turn text the model saw.
2. `src/leon_pattern_miner/cie_codebook.json`
   - source of truth for codes and examples.
3. `benchmark/cie-extraction-v0/`
   - public-safe scoring fixture preserving the v0 shape: 15 sessions, 287 turns, 51 reference findings.
   - raw conversation-derived benchmark data stays local/ignored because the GitHub repo is public.
4. `scripts/run_benchmark.py`
   - canonical candidate-model runner for quality comparison.
   - supports explicit `--pass-strategy per_family|combined`; default is `per_family`, matching the corpus CIE harness.
   - supports `--adapter xai` with authenticated `/v1/models` preflight and retry-aware `--max-model-calls` budgeting.

## Legacy path warning

`miner extract --use-llm` uses `src/leon_pattern_miner/llm_extractors.py`, which selects/truncates candidate turns and uses a thin prompt. It is a legacy/pilot/provider-smoke path, not the canonical model-quality path.

The CLI now enforces that boundary: any `--use-llm` run requires `--run-purpose provider-smoke`; `extraction-quality` and `corpus-production` are blocked through the legacy path. Remote legacy smoke runs are capped and use retry-aware provider call ceilings.

Do not use that path to decide whether Grok, Qwen, DiffusionGemma, or any other frontier/local model is good at conversation intelligence extraction. Use it only for narrow provider-mechanics tests, and label results as such.

## Root cause of the 2026-06-15 Grok mishap

The xAI/Grok adapter work added provider mechanics to `miner extract --use-llm`. I then ran none/low/high reasoning tests through that convenient CLI instead of first asking whether the extractor was the right harness.

That sent Grok a regex-selected, truncated candidate-turn sketch instead of CIE windows with codebook/few-shots. The resulting record counts measure the legacy extractor path, not Grok's real usefulness for the north star.

Corrective guardrails now implemented in code:

- CIE prompt rendering returns quote-source metadata; validation can verify quotes against prompt-visible cleaned/masked turn text.
- Benchmark and corpus CIE share explicit pass strategy semantics; benchmark defaults to `per_family` instead of always `all`.
- Standalone model-routing signals route into the authorization/model-routing CIE pass.
- Tool-only evidence cannot justify `source_reliability=A`; use `D` for tool output.
- Deterministic extractor IDs are versioned as `deterministic-v2` and include stream/pattern/actor/normalized summary/evidence so distinct patterns sharing a quote do not collide while template-like duplicates still dedupe.
- Legacy `miner extract --use-llm` is code-enforced as provider-smoke-only with retry-aware remote provider budgeting.
- xAI/Grok is wired into `scripts/run_benchmark.py` / `src/leon_pattern_miner/adapters.py`; use that path for CIE benchmark runs, not the legacy extractor.

## Current model/evaluation status

- The public CIE benchmark fixture exists for harness/scorer regression. Actual model-quality claims require a private/sanitized CIE gold-set recall gate.
- Private C0 Qwen-vs-Opus baseline: recall about 0.35, quote-strict about 0.29, agreement-with-Opus about 0.62. The public fixture preserves that score shape for regression only; Opus is a reference, not ground truth.
- DiffusionGemma local runtime exists but did not emit a valid CIE JSON envelope on a real CIE window; do not run/report a full scorecard yet.
- Grok 4.3 provider adapter mechanics and CIE benchmark adapter plumbing are implemented. The prior 50-session none/low/high reasoning results remain legacy-harness results only and should not be used to judge extraction quality or production readiness.

## Outcome attribution extension

`outcome_attribution` is a new additive CIE family for arc/session-level intent → delivery → cause records. It measures stated intent, whether delivery landed/was partial/needed rework/failed, and the attributed cause of shortfalls. The cause facet can explicitly name `leon_instruction` when the transcript supports that Leon's ambiguous or contradictory instruction caused rework or failure. Current status: implemented behind synthetic tests only; no live model calls, private transcript data, or production corpus runs have been performed.

## Next safe step

Run Grok 4.3 against a private/sanitized CIE gold-set dataset through `scripts/run_benchmark.py --adapter xai --pass-strategy per_family` with explicit `--max-model-calls`, cost/latency tracking, and Leon approval before any paid/off-machine prompt spend beyond a bounded smoke.

Use the public `benchmark/cie-extraction-v0/` fixture only for harness mechanics/regression unless/until a per-family public-safe fixture is generated.

Required report shape for a real quality run:

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
