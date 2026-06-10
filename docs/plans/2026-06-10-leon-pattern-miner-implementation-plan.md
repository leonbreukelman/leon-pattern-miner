# Leon Pattern Miner Implementation Plan

> **For Hermes:** Use disciplined-project-delivery and test-driven-development. This project mines local AI-agent conversations into evidence-backed autonomy policy candidates. It does not auto-promote anything into Hermes memory/skills.

**Goal:** Build a recoverable local miner that extracts steering, agent-behavior, and methodology patterns from Leon's AI-agent conversations, runs a 20-session pilot, obtains Fable review, then gates full-corpus mining behind explicit approval.

**Architecture:** Local Python CLI + SQLite queue/state DB + deterministic extractors + optional local OpenAI-compatible llama.cpp extractor. Raw real-session outputs stay local and ignored. Git contains only source, tests, docs, and synthetic fixtures.

**Tech Stack:** Python 3.11+, stdlib SQLite/argparse/urllib, pytest, llama.cpp server on RTX 4090, Claude Code Fable for review.

## Fable plan verdict

Fable approved the concept with required adjustments:

1. Run sensitivity masking before any LLM call, including local calls.
2. Make pilot approval structural: full-corpus commands refuse until `pilot_approved=true` in the miner DB.
3. Hard-reject any LLM record whose evidence quote is not a verbatim source substring.
4. Commit only synthetic fixtures; keep real reports/local DB ignored.
5. Exclude this miner's own sessions by default.
6. Include deterministic-vs-LLM agreement metrics in pilot reports.
7. Keep methodology stream constrained during pilot.

## Phase 0 — Scaffold and TDD harness

Acceptance:
- `uv run pytest` passes.
- CLI can ingest a synthetic session, show status, and produce deterministic records.
- Work queue can reset stale running work.

## Phase 1 — 20-session pilot

Acceptance:
- Select at least 20 non-miner Hermes sessions from local state.
- Run deterministic extraction and local LLM extraction where the endpoint is healthy.
- Produce local ignored report under `reports/pilot-001.md`.
- Include stream counts, top clusters, evidence examples, sensitivity counts, errors/retries, and deterministic-vs-LLM agreement.

## Phase 2 — Fable gate

Acceptance:
- Save Fable review under `docs/verification/` if it contains no real transcript quotes, otherwise under ignored `reports/`.
- Only set `pilot_approved=true` if review verdict is ACCEPT/APPROVE.

## Phase 3 — Full corpus

Acceptance:
- Full run refuses before pilot approval.
- After approval, full run processes all eligible local sessions resumably.
- Monitor records status and retries failures without duplicating records.

## Recovery/monitoring

- `miner status` reports sessions, turns, records, queue, approvals, and errors.
- `miner retry-failed` moves failed work back to pending.
- `miner run --resume` resets stale running items and continues.
- `scripts/monitor_once.sh` is suitable for systemd/cron wrapping.
