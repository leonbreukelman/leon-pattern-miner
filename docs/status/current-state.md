# Current state — leon-pattern-miner

Date: 2026-06-17

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

## Retired session-level model extractor

The former session-level model extractor has been removed from active code, CLI flags, monitor scripts, active reports, and active steering docs. Do not reintroduce ad hoc session extraction for provider-smoke, model-quality, or corpus-production work.

Provider mechanics and quality checks now use the CIE benchmark/adapter path. Keep live provider runs bounded with explicit call ceilings, privacy/spend approval, and clear provider-mechanics or extraction-quality labeling.

## Root cause of the 2026-06-15 Grok mishap

The xAI/Grok adapter work first attached provider mechanics to the wrong session-level harness. I then ran none/low/high reasoning tests through that convenient route instead of first asking whether the extractor was the right harness.

That sent Grok a regex-selected, truncated candidate-turn sketch instead of CIE windows with codebook/few-shots. The resulting record counts measure the legacy extractor path, not Grok's real usefulness for the north star.

Corrective guardrails now implemented in code:

- CIE prompt rendering returns quote-source metadata; validation can verify quotes against prompt-visible cleaned/masked turn text.
- Benchmark and corpus CIE share explicit pass strategy semantics; benchmark defaults to `per_family` instead of always `all`.
- Standalone model-routing signals route into the authorization/model-routing CIE pass.
- Tool-only evidence cannot justify `source_reliability=A`; use `D` for tool output.
- Deterministic extractor IDs are versioned as `deterministic-v2` and include stream/pattern/actor/normalized summary/evidence so distinct patterns sharing a quote do not collide while template-like duplicates still dedupe.
- The old session-level model extraction entrypoint is retired from active code and CLI surfaces; benchmark/adapter tests guard the CIE/xAI route instead.
- xAI/Grok is wired into `scripts/run_benchmark.py` / `src/leon_pattern_miner/adapters.py`; use that path for CIE benchmark runs.

## Current model/evaluation status

- The public CIE benchmark fixture exists for harness/scorer regression. Actual model-quality claims require a private/sanitized CIE gold-set recall gate.
- 2026-06-17 Opus CIE smoke: Claude Code `--model opus` (`modelUsage: claude-opus-4-8`) successfully ran canonical CIE prompt/JSON/quote-validation plumbing on public/synthetic data only. Artifact: `reports/opus-cie-smoke-2026-06-17/report.md`. This was provider/mechanics smoke only, not a model-quality claim.
- 2026-06-17 Grok 4.3 private CIE real job: after explicitly sourcing `.env`, `grok-4.3` high reasoning ran on the private/sanitized CIE v0 gold set with `--max-model-calls 248` for 124 per-family prompts. Result: code-level recall 0.588 (30/51), quote-strict recall 0.373 (19/51), agreement-with-Opus 0.448, valid-JSON window rate 1.0, elapsed 16m52s. Artifact: `reports/grok43-private-cie-realjob-2026-06-17/report.md`. Caveat: actual provider token/cost usage was not persisted by the current runner.
- 2026-06-17 xAI cost reporting fix: the benchmark xAI path now auto-loads repo `.env` without overriding exported env vars, aggregates `usage.cost_in_usd_ticks`, cached/reasoning tokens, priced/unpriced calls, and writes `provider-usage.json` plus `provider_usage` in scorecards. A one-call Grok 4.3 high-reasoning probe confirmed `/v1/chat/completions` returns exact cost ticks (`cost_source=exact`, cost `$0.00303405`). A regression test now proves cost-cap breach stops before a second provider request and persists `cost_cap_breached=true`. Opus final re-review verdict: ACCEPT. Artifact: `reports/xai-cost-reporting-implementation-2026-06-17/implementation-report.md`.
- Private C0 Qwen-vs-Opus baseline: recall about 0.35, quote-strict about 0.29, agreement-with-Opus about 0.62. The public fixture preserves that score shape for regression only; Opus is a reference, not ground truth.
- DiffusionGemma local runtime exists but did not emit a valid CIE JSON envelope on a real CIE window; do not run/report a full scorecard yet.
- Grok 4.3 provider adapter mechanics and CIE benchmark adapter plumbing are implemented. The prior 50-session none/low/high reasoning results remain legacy-harness results only and should not be used to judge extraction quality or production readiness.
- Grok 4.3 invalid-diff output remains suspected/unvalidated only; do not build a regression or routing rule for it until the raw Grok output/diff artifact is found or the behavior is reproduced.

## Tool-issue remediation status

2026-06-17 validated tool issues now have code-level guardrails where the data was sufficient:

- CIE rejected records are persisted forward-only in `cie_rejections` with `rejection_cause` and `record_json`; fixture tests reconcile `cie_window_runs.records_rejected` against persisted rejection rows.
- Errored CIE windows can be selected offline via `errored_cie_window_runs()` so the historical 95-window rerun has a deterministic, testable target list. The actual rerun remains Leon-gated and was not executed.
- `CIERunSummary` records `pass_strategy` and `no_signal_windows_diagnostic`; in combined mode, `no_signal_windows=0` is explicitly non-diagnostic because combined mode runs the all-family pass by construction.

## Outcome attribution extension

`outcome_attribution` is a new additive CIE family for arc/session-level intent → delivery → cause records. It measures stated intent, whether delivery landed/was partial/needed rework/failed, and the attributed cause of shortfalls. The cause facet can explicitly name `leon_instruction` when the transcript supports that Leon's ambiguous or contradictory instruction caused rework or failure. Current status: implemented behind synthetic tests only; no live model calls, private transcript data, or production corpus runs have been performed.

## Public data scrub status

HEAD no longer carries real Hermes session IDs or real transcript quotes in the CIE codebook few-shots. Those examples are synthetic teaching data now, and `tests/test_no_raw_session_data.py` guards tracked source/docs/tests/benchmark/scripts files against reintroducing real `hermes:` session IDs or non-`synthetic:`/`fixture:` JSON `turn_id` values. The two DiffusionGemma smoke docs use a synthetic window label. This cleans the current tree only; the old data remains in public git history and any history rewrite/BFG/filter-repo cleanup is a separate Leon-only decision.

## Next safe step

Run the next Grok 4.3 high-reasoning mining/evaluation job through `scripts/run_benchmark.py --adapter xai --pass-strategy per_family` with explicit `--max-model-calls` and `--cost-cap-usd`; the runner now persists exact xAI cost ticks when present. Leon approval is still required before a new paid/off-machine batch run.

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
