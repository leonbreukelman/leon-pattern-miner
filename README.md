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
- `scripts/run_benchmark.py` — canonical runner for candidate models.

Because this GitHub repo is public, raw conversation-derived benchmark sessions are not checked in. Quality claims must use a private/sanitized CIE gold-set recall gate and report code-level recall, quote-strict recall, agreement-with-reference, per-bucket results, cost, latency, and caveats. Opus reference findings are a strong reference, not ground truth.

## Legacy/pilot path warning

`miner extract --use-llm` currently routes through `src/leon_pattern_miner/llm_extractors.py`. Treat that as legacy pilot/provider-smoke scaffolding, not the canonical extraction-quality harness.

That path selects/truncates keyword-matched candidate turns and uses a thin prompt. It is acceptable for narrow provider-mechanics checks — authentication, JSON envelope, masking, call budget, quote-validation plumbing — but not for deciding whether Grok/Qwen/DiffusionGemma or any other model is good at the north-star task.

Before any model run, decide which question is being answered:

1. **Provider mechanics:** copied DB, tiny bounded run, explicit call ceiling, label results provider-smoke only.
2. **Extraction quality:** CIE benchmark or equivalent gold-set recall gate.
3. **Corpus production:** only after a quality gate, copied-DB dry run, call/spend ceiling, privacy check, and explicit Leon approval for paid/off-machine prompts.

## Conservative/safety boundaries

- reads local conversation archives;
- stores extracted records in local SQLite / frozen benchmark artifacts;
- writes reports under `reports/`;
- never auto-promotes records into Hermes memory or skills;
- promotion requires quote verification, governance, and Leon sign-off through the write-approval gate.

## Historical streams

The original session-level extractor used these coarse streams:

1. **Leon steering** — recurring questions, directions, corrections, authorization semantics, escalation/non-escalation rules.
2. **Agent behavior** — clarification triggers, failure/recovery arcs, wasted loops, verification habits.
3. **Methodology** — emerging project-building methods across Leon projects.

The active CIE path uses the richer codebook in `src/leon_pattern_miner/cie_codebook.json`.

## Local runtime note

The local OpenAI-compatible runtime historically used for Qwen/provider-smoke work is `127.0.0.1:8080`, preferably `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` on the RTX 4090 with `-c 8192`, `-np 1`, `--no-mmproj`, and thinking disabled. Use this as an adapter/runtime fact, not as permission to evaluate extraction quality through the legacy session extractor. Set `LLM_EXTRACTOR_VERSION` when intentionally comparing/replaying another legacy extractor run.

## Current state

Read `AGENTS.md` and `docs/status/current-state.md` before non-trivial work. Dated files under `docs/plans/` and `docs/status/` are audit history unless the current-state file says otherwise.
