# Current state — leon-pattern-miner

Date: 2026-06-20

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
- 2026-06-17 Grok 4.3 private CIE real job: after explicitly sourcing `.env`, `grok-4.3` high reasoning ran on the private/sanitized CIE v0 gold set with `--max-model-calls 248` for 124 per-family prompts. Result: code-level recall 0.588 (30/51), quote-strict recall 0.373 (19/51), agreement-with-Opus 0.448, valid-JSON window rate 1.0, elapsed 16m52s. Artifact: `reports/grok43-private-cie-realjob-2026-06-17/report.md`. Caveat: the run report did not persist exact provider token/cost usage; later cost-reporting work added that persistence.
- 2026-06-17 xAI cost reporting fix: the benchmark xAI path now auto-loads repo `.env` without overriding exported env vars, aggregates `usage.cost_in_usd_ticks`, cached/reasoning tokens, priced/unpriced calls, and writes `provider-usage.json` plus `provider_usage` in scorecards. A one-call Grok 4.3 high-reasoning probe confirmed `/v1/chat/completions` returns exact cost ticks (`cost_source=exact`, cost `$0.00303405`). A regression test now proves cost-cap breach stops before a second provider request and persists `cost_cap_breached=true`. Opus final re-review verdict: ACCEPT. Artifact: `reports/xai-cost-reporting-implementation-2026-06-17/implementation-report.md`.
- 2026-06-17 xAI cost-estimator 10-conversation verification: Grok 4.3 high reasoning ran on a copied public-safe 10-session CIE subset with `--max-model-calls 20` and `--cost-cap-usd 0.50`. Provider usage persisted correctly: 10/10 calls priced, exact `cost_in_usd_ticks=290543500`, exact cost `$0.02905435`, `cost_source=exact`, no cost-cap breach, elapsed 25.89s. Extraction yield on this synthetic subset was poor: valid JSON rate 1.0 but 0 records / 38 gold findings. Root cause: the public fixture contains scrubbed placeholder text, not semantic conversation content, and `per_family` auto-selection only prompted `verification_review`; that made 33/38 gold findings structurally unreachable and the remaining 5 were not semantically supported by the placeholder text. Treat this as cost-instrumentation verified, not an extraction-quality pass or model-regression signal. Artifact: `reports/xai-cost-estimator-10-conv-2026-06-17/report.md`.
- 2026-06-17 latest-10 real Hermes CIE mining run: Grok 4.3 high reasoning ran on the actual latest 10 Hermes sessions from local `~/.hermes/state.db` in a fresh ignored DB, with `--max-model-calls` equivalent 240 and cost cap `$2.00`. Result: 10 sessions, 358 filtered turns, 31 windows, 120 per-family passes, 48 automated quote-validated records across 7/10 sessions, 15 validator guardrail rejections (`quote_not_found`), 0 errors, exact xAI cost `$1.13624755`, elapsed 1102s. No precision/recall claim: these records are not human-adjudicated or gold-scored. Records are local/private and not promoted. Artifact: `reports/latest10-hermes-real-mining-2026-06-17/report.md`.
- 2026-06-17 attempted Opus 4.8 max comparison on the same latest-10 real Hermes CIE plan: exact aliases `opus4.8`/`opus-4.8` were unavailable, but `claude --model opus --effort max` preflight reported actual modelUsage `claude-opus-4-8`. The full 120-pass comparison did not complete: Claude Code hit a 429 session limit after a clean parallel attempt reached 20 processed passes, 31 accepted records, 2 validator rejections, and 30 session-limit error rows; last progress cost lower-bound `$6.7425625`. Treat all Opus/Grok agreement numbers from this artifact as partial only, not apples-to-apples quality. Artifact: `reports/latest10-hermes-opus48-max-compare-2026-06-17/partial-report.md`.
- 2026-06-17 latest-10 real Hermes Grok 4.3 high run with frontier-sized windows: reran the same 10 sessions and same `per_family` strategy with `max_window_tokens=170000`, `overlap_tokens=20000`, and `max_prompt_tokens=190000`. All 10 sessions fit in one window; actual max window estimate was only 23,236 tokens and max prompt estimate 24,723, so the tested change is whole-session windows vs 3.5k chunking, not near-170k prompts. Result: 10 windows, 38 passes, 34 accepted records across 7/10 sessions, 6 `quote_not_found` rejections, 0 errors, exact xAI cost `$0.78290705`, elapsed 467s. Compared with the 3.5k run: -82 calls, -14 records, -$0.35334050 cost, code-level overlap 26 records, quote-overlap 17 records. Opus sanitized review found the arithmetic/framing faithful and no corrections required; interpretation caveats were patched into the report. Artifact: `reports/latest10-hermes-real-mining-170k-grok43-2026-06-17/report.md`.
- 2026-06-18 $20 full-Hermes breadth-first CIE collection run: Leon authorized a `$20` Grok 4.3 high-reasoning budget to start with the freshest conversations and work older until the budget ceiling or corpus end. A no-call plan found 1,670 sessions / 27,479 filtered turns; all sessions fit one whole-session window. Full canonical `per_family` would require 6,662 prompts and was estimated around `$137`, so the live run used a `combined` all-family pass to maximize coverage under budget. Result: 1,534 / 1,670 sessions attempted newest-to-oldest (91.86%), 1,530 successful processed prompts, 4 errored prompts, 1,352 automated quote-validated candidate records across 964 sessions, 695 validator rejections, elapsed 8.51h. Cost: 1,532 / 1,534 calls returned exact xAI ticks; exact priced cost `$17.65011150`, estimator/effective cost `$19.97388750`, `cost_source=partial`, cap not breached, stopped by pre-call reserve with 136 oldest sessions unscanned. Caveat: this is broad corpus collection, not gold-scored quality or promotion-ready intelligence; records remain local/private. Artifact: `reports/full-hermes-budget20-grok43-2026-06-17/report.md`.
- 2026-06-19 outcome-attribution real-data proof for architect review: local-only `runtime/outcome-proof-v0/` dataset built from `~/.hermes/state.db`, newest-first 10 real arc-bearing sessions / 476 filtered turns, empty gold by design. Grok 4.3 high reasoning via `scripts/run_benchmark.py --adapter xai --pass-strategy per_family` ran 49 prompted family passes with retry-aware ceiling 98 and `$5` cap. Result: 48 accepted records, including `intent_stated=7`, `delivery_result=3`, `rework_cause=1`; 18 validator rejections (`quote_not_found=10`, `turn_id_not_found=8`); 0 transport errors; exact xAI cost `$0.97410550`. Full prompts, raw responses, validator decisions, and real source turns are in `runtime/outcome-proof-v0/ARCHITECT-REVIEW.md` (ignored/local-only, not for git). This is an extraction proof, not a scored quality gate.
- 2026-06-19 outcome-attribution Iteration 1: same frozen local proof dataset, prompt/codebook-only cause-lift plus the sanctioned exact-quote turn-id rebinding path. Grok 4.3 high reasoning rerun used the same 49-pass `per_family` config and exact xAI cost `$1.00759710`. Result: `delivery_result` increased 3→5, `rework_cause` stayed 1→1 but now captures the clear wrong-primary-path rework case, and rejections dropped from `quote_not_found=10` / `turn_id_not_found=8` to `quote_not_found=5` / `turn_id_not_found=0` with 6 accepted evidence rebindings. Guard test and full suite passed (`119 passed`). Opus reviewed every Iteration 1 delivery/cause record, found no fabricated causes, and signed off `ACCEPT_WITH_CAVEATS` because one later shortfall arc remains a recall gap. Artifact: `runtime/outcome-proof-v0/ARCHITECT-REVIEW-iter1.md` (ignored/local-only, not for git).
- 2026-06-19 mine1 operationalization: `miner mine` now performs an unattended local cycle: ingest only newer Hermes sessions from `~/.hermes/state.db` with pattern-miner self-exclusion, run current CIE extraction through Grok 4.3 high / xAI / `per_family` with retry-aware call ceiling and cost cap, dedupe into the persistent `cie_records` register, advance `last_processed_session_started_at` only after clean ingest/extraction, and refresh `runtime/findings-report.md`. Real mine1 run processed 10 newest eligible real conversations, 141 primary Grok calls plus 1 retry call, exact combined xAI cost `$1.42994715`, 61 new quote-validated rows, register totals 2,898 rows / 2,924 occurrences / 26 dedup-collapsed occurrences under the original statement-sensitive key. A third run proved idempotency with 0 selected sessions, 0 prompts, 0 new records. Full suite passed (`128 passed`). Disabled schedule templates are under `runtime/schedule/`. Opus scoped review signed off `PASS` for the operational plumbing. Artifact: `runtime/ARCHITECT-REVIEW-mine1.md` (ignored/local-only, includes real ids/quotes, not for git).
- 2026-06-20 dedup re-key: the active `cie_records` register now uses an evidence-stable `cie_dedup_v2_` key: normalized `codebook_code` plus the full sorted normalized evidence-quote set when multiple substantive quotes exist, otherwise the single normalized substantive quote, with normalized statement retained only as a weak-anchor guard for trivial short quotes. Existing `runtime/miner.db` was restored from a timestamped pre-rekey backup and re-keyed offline; rows dropped 2,898→2,436 while total occurrences were conserved at 2,924. A read-only-review split consolidated materially (single-quote cluster 75 rows / 85 occurrences → one row / count 85). Focused dedup/rekey tests and full suite pass (`132 passed`). Claude Code Opus reviewed the revised formula, tests, migration evidence, and five largest merged clusters; verdict `ACCEPT`. Local artifact: `runtime/ARCHITECT-REVIEW-dedup.md`. The schedule remains disabled pending architect confirmation.
- 2026-06-20 system/platform primary-evidence purge: the CIE validator now rejects records whose primary evidence quote is an exact/anchored platform telemetry string rather than Leon/agent behavior. The local `runtime/miner.db` register was backed up, then purged offline: rows dropped 2,436→2,406 and total occurrences dropped 2,924→2,867 by removing 30 platform rows / 57 platform occurrences; non-denylisted behavioral occurrences reconciled exactly. The removed clusters were tool-iteration limit notices, tool-loop watchdog warnings, hardline shutdown/reboot block notices, foreground-timeout-cap notices, model-switch notes, and the bare voice-input concision note. Focused guard tests and full suite pass (`136 passed`). Claude Code Opus approved the denylist, pre-purge deletion set, tests, and architect artifact. Local artifact: `runtime/ARCHITECT-REVIEW-sysmsg-purge.md`. The schedule remains disabled pending architect confirmation of the cleaned register.
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

`outcome_attribution` is a new additive CIE family for arc/session-level intent → delivery → cause records. It measures stated intent, whether delivery landed/was partial/needed rework/failed, and the attributed cause of shortfalls. The cause facet can explicitly name `leon_instruction` when the transcript supports that Leon's ambiguous or contradictory instruction caused rework or failure. The 2026-06-18 combined full-Hermes collection run produced no accepted outcome-attribution code and 620 `outcome_facets_invalid` validator rejections because the rendered output schema showed empty `facets` and prompt rules did not teach the delivery/cause contract. PR #12 / branch `fix/outcome-facets-prompt` tightened the prompt path only: the rendered schema now documents required `facets.delivery`/`facets.cause`, the prompt states the outcome facet rule, and the combined pass keeps a populated synthetic `rework_cause` few-shot visible. Iteration 1 then added guarded cause-lift prompt/few-shot examples and a sanctioned evidence rebinding step: if a cited turn id is missing or wrong, the validator may accept only when the same quote is found verbatim in the window's validation surface; zero-match quotes still reject as `quote_not_found`. Focused guard tests and the full suite pass. The 2026-06-19 proof artifacts show real outcome records under `per_family`, but still do not establish precision/recall because gold is empty and records are only Opus-critic-reviewed, not adjudicated gold.

Counts-only diagnostic from the existing private Grok 4.3 CIE real-job scorecard: the 0.588 code-level recall decomposes into 30 / 51 matched gold findings, 16 prompted-but-missed gold findings, and 5 family/code-never-prompted gold findings under the run's `per_family` scoring semantics. The private v0 gold set has zero outcome-attribution gold records, so this explains the prior recall run's detection-coverage confound but does not measure the outcome-facets prompt fix. Local ignored artifact: `reports/outcome-facets-prompt-fix-2026-06-18/prompted-family-decomposition.md`.

## Public data scrub status

HEAD no longer carries real Hermes session IDs or real transcript quotes in the CIE codebook few-shots. Those examples are synthetic teaching data now, and `tests/test_no_raw_session_data.py` guards tracked source/docs/tests/benchmark/scripts files against reintroducing real `hermes:` session IDs or non-`synthetic:`/`fixture:` JSON `turn_id` values. The two DiffusionGemma smoke docs use a synthetic window label. This cleans the current tree only; the old data remains in public git history and any history rewrite/BFG/filter-repo cleanup is a separate Leon-only decision.

## Next safe step

The 2026-06-18 `$20` full-Hermes combined pass produced broad local candidate records, not adjudicated memory/skill material. The 2026-06-19 mine1 work makes recurring local collection operational, the 2026-06-20 dedup re-key makes frequency ranking meaningful, and the 2026-06-20 system/platform purge removes primary-evidence telemetry from the register; none of these approve automatic promotion to Hermes memory/skills. Next safe step is architect confirmation of `runtime/ARCHITECT-REVIEW-sysmsg-purge.md` and the cleaned register. Outcome-attribution has local real-data proof artifacts (`runtime/outcome-proof-v0/ARCHITECT-REVIEW.md` and `ARCHITECT-REVIEW-iter1.md`) showing accepted outcome records after the prompt fix and Iteration 1 cause/rebinding repairs, but it is still not a precision/recall gate. Do not enable the nightly schedule until Leon explicitly switches it on after architect confirmation. Any next paid/off-machine quality run should still use explicit `--max-model-calls` and `--cost-cap-usd`; Leon approval is still required for additional paid/off-machine batch spend outside the authorized mine cycle.

Use the public `benchmark/cie-extraction-v0/` fixture only for harness mechanics/regression unless/until a per-family public-safe fixture is generated.

Required report shape for a real quality run:

- model and reasoning mode;
- exact benchmark command;
- code-level recall and quote-strict recall vs Opus reference;
- agreement-with-Opus;
- per-bucket short/medium/long results;
- cost and latency;
- caveats, including small v0 benchmark size and Opus-reference-not-ground-truth.

Do not treat the 2026-06-18 full-corpus combined pass as a quality gate or as promotion approval; it is a local candidate-collection artifact that needs adjudication.

## Historical docs policy

Dated files under `docs/plans/`, `docs/status/`, `docs/model-facts/`, and `docs/prompts/` are retained as audit history. They are not automatically current. When they conflict with this file or `AGENTS.md`, follow this file and `AGENTS.md`.

Artifact paths under `reports/` and DB paths under `runtime/` are local ignored evidence unless a future PR explicitly says a sanitized report artifact was published. Public docs may reference those paths for local auditability, but raw record JSON, transcript quotes, copied Hermes DBs, and provider logs stay out of git by default.
