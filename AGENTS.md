# AGENTS.md — leon-pattern-miner

## North star

This repository exists to extract durable patterns and intelligence from Leon/Hermes conversations so future agents can act better: preferences, steering, authorization boundaries, model-routing rules, verification habits, workflow patterns, and other reusable operating intelligence.

The deliverable is not "a model ran" or "records were counted". The deliverable is evidence-backed, quote-verified, evaluated conversation intelligence that can later flow through governance into memory/skills only with Leon sign-off.

## Canonical extraction/evaluation path

For model-quality or extraction-quality work, use the CIE/benchmark path:

- `src/leon_pattern_miner/cie.py`
  - `build_session_windows()` covers conversation windows with overlap.
  - `render_cie_prompt()` / `render_cie_prompt_bundle()` use the codebook, few-shots, near-misses, schema, and quote rules.
  - `validate_cie_payload()` verifies exact quote evidence and schema against prompt-visible quote sources when provided.
- `src/leon_pattern_miner/cie_codebook.json`
  - Canonical code definitions, positive examples, and negative/near-miss examples.
- `benchmark/cie-extraction-v0/`
  - Public-safe fixture preserving the v0 scoring shape: 15 sessions, 287 turns, 51 reference findings.
  - Because this GitHub repo is public, raw conversation-derived benchmark sessions must stay local/ignored unless explicitly sanitized and approved.
- `scripts/run_benchmark.py`
  - Canonical runner for comparing candidate models against the public fixture or a private/sanitized CIE gold set; supports explicit pass strategy and xAI adapter plumbing.

Before claiming a model is good or bad at mining, run it through a private/sanitized CIE gold-set recall gate, not merely the public synthetic fixture. Report code-level recall, quote-strict recall, agreement-with-reference, per-bucket results, cost, latency, and caveats.

## Retired session-level model extractor

The former session-level model extractor has been removed from active code and CLI surfaces. Do not reintroduce an ad hoc session extractor for model-quality, provider-smoke, or corpus work.

Provider mechanics now belong in the canonical benchmark/adapter path: `scripts/run_benchmark.py`, `src/leon_pattern_miner/adapters.py`, and `src/leon_pattern_miner/llm.py`. Any live provider check must use explicit call ceilings, privacy/spend approval, and clear provider-mechanics labeling.

## Required decision gate before any model run

Before running a model, state which question is being answered:

1. Provider mechanics? Use a copied DB, tiny bounded run, explicit call ceiling, and label it provider-smoke only.
2. Extraction quality? Use CIE/benchmark or a gold-set recall gate. Do not use ad hoc session extraction.
3. Corpus production? Requires a prior quality gate, copied-DB dry run, call/spend ceiling, privacy check, and explicit Leon approval for paid/off-machine prompts.

If the question is ambiguous, default to extraction quality and use the CIE benchmark.

## Documentation hygiene

- Read `README.md` and `docs/status/current-state.md` before non-trivial work.
- Treat dated plans/status files as historical unless `docs/status/current-state.md` says they are active.
- When a run changes the state, update `docs/status/current-state.md` in the same turn.
- Keep dated reports under `reports/`; keep durable/current orientation under README, AGENTS.md, and `docs/status/current-state.md`.
- Do not leave stale "ready for production" or "next step" claims in active docs after a better root cause is found.
- Do not promote mined content to Hermes memory/skills from this repo without Leon sign-off and the write-approval gate.

## Verification norms

- For code changes: TDD where practical, then `uv run pytest -q`; run narrower focused tests first when appropriate.
- For docs/context changes: inspect all active docs, patch stale/conflicting claims, then run a grep-style consistency check for forbidden or stale phrases.
- For non-trivial deliverables: save an artifact under `docs/` or `reports/`, run one independent Opus review, patch valid criticism, then report.
