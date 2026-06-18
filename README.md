# leon-pattern-miner

Private local conversation-intelligence miner for Leon/Hermes transcripts.

## North star

Extract durable, evidence-backed patterns and intelligence from conversations so future agents can operate better: Leon's steering, preferences, authorization boundaries, model-routing rules, agent behavior patterns, verification habits, and reusable workflow methods.

The project is not a generic model bakeoff. A model run matters only when it improves quote-verified conversation intelligence extraction.

## Current canonical path

For extraction-quality or model-quality work, use the CIE/benchmark path:

- `src/leon_pattern_miner/cie.py` — conversation windowing, CIE prompt rendering, quote/schema validation.
- `src/leon_pattern_miner/cie_codebook.json` — canonical codes, definitions, few-shot examples, and near-misses.
- `benchmark/cie-extraction-v0/` — public-safe benchmark fixture preserving the v0 scoring shape: 15 sessions, 287 turns, 51 reference findings.
- `scripts/run_benchmark.py` — canonical runner for candidate models, including explicit `--pass-strategy per_family|combined` and `--adapter xai` support.

Because this GitHub repo is public, raw conversation-derived benchmark sessions are not checked in. Quality claims must use a private/sanitized CIE gold-set recall gate and report code-level recall, quote-strict recall, agreement-with-reference, per-bucket results, cost, latency, and caveats. Opus reference findings are a strong reference, not ground truth.

## Retired session-level model extractor

The former session-level model extractor has been removed from active code and CLI surfaces. Keep provider mechanics and model-quality work on the CIE benchmark/adapter path instead of reintroducing ad hoc session extraction.

If you previously used `miner extract --use-llm` or the full-corpus monitor scripts, migrate to `scripts/run_benchmark.py` with an explicit dataset, adapter, pass strategy, and provider call/cost ceiling. Historical SQLite tables such as `llm_session_runs` may remain in old local databases as inert leftovers, but active status/report paths no longer read them.

Before any model run, decide which question is being answered:

1. **Provider mechanics:** copied DB, tiny bounded run, explicit call ceiling, label results provider-smoke only.
2. **Extraction quality:** CIE benchmark or equivalent gold-set recall gate.
3. **Corpus production:** only after a quality gate, copied-DB dry run, call/spend ceiling, privacy check, and explicit Leon approval for paid/off-machine prompts.

## Conservative/safety boundaries

- reads local conversation archives;
- stores extracted records in local SQLite / frozen benchmark artifacts;
- writes detailed run reports under ignored/local-only `reports/` by default;
- commits only sanitized status/planning docs to this public repository unless a report is explicitly scrubbed and approved for publication;
- never auto-promotes records into Hermes memory or skills;
- promotion requires quote verification, governance, and Leon sign-off through the write-approval gate.

## Historical streams

The original session-level extractor used these coarse streams:

1. **Leon steering** — recurring questions, directions, corrections, authorization semantics, escalation/non-escalation rules.
2. **Agent behavior** — clarification triggers, failure/recovery arcs, wasted loops, verification habits.
3. **Methodology** — emerging project-building methods across Leon projects.

The active CIE path uses the richer codebook in `src/leon_pattern_miner/cie_codebook.json`.

## Local runtime note

The local OpenAI-compatible runtime historically used for Qwen checks is `127.0.0.1:8080`, preferably `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` on the RTX 4090 with `-c 8192`, `-np 1`, `--no-mmproj`, and thinking disabled. Use this as an adapter/runtime fact for benchmark runs, not as a separate extraction harness.

## Current state

Read `AGENTS.md` and `docs/status/current-state.md` before non-trivial work. Dated files under `docs/plans/` and `docs/status/` are audit history unless the current-state file says otherwise.
