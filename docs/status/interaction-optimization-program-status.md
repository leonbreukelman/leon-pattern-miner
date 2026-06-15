# Status: Interaction-Optimization + CIE Governance program

Owner: Leon | Lead dev: Claude (Hermes) | Started: 2026-06-13
Goal artifact: docs/prompts/standing-goal-interaction-optimization-2026-06-13.md

This file is updated at every phase gate so progress survives compaction/model-switch.

Current orientation note (2026-06-15): the north star is evidence-backed conversation intelligence extraction. For model-quality decisions, use the CIE/benchmark path (`cie.py`, `cie_codebook.json`, `scripts/run_benchmark.py`) against a private/sanitized gold set. The checked-in `benchmark/cie-extraction-v0` payload is a public synthetic fixture for harness regression only. Do not use the legacy `miner extract --use-llm` session extractor to judge model quality; it is provider-smoke scaffolding only.

## Legend
[x] done & verified | [~] in progress | [ ] not started

## Phases
- [x] PHASE A — routing + auto-review doctrine (SOUL + leon-model-routing-and-review)
- [x] PHASE B — reusable one-command critic loop (leon-critic-loop + script)
- [x] PHASE D — capability preflight + bg-event interpreter (leon-ops-preflight + 2 scripts)
- [~] PHASE C0 — recall gate (gold set: Qwen vs Opus precision/recall/F1)  ← NEXT, has a real decision
- [ ] PHASE C1..C6 — rejections, triage, sensitivity, clustering, adjudication, rerun, promotion queue
- [ ] PHASE E — pluggable memory evaluation

## PHASE A — DONE (verified)
- ~/.hermes/SOUL.md: "Standing doctrine for Leon" (autonomy, routing, review reflex, safety).
- Skill leon-model-routing-and-review (enabled). Self-reviewed by Opus (Fable preflight ->
  UNAVAILABLE -> Opus fallback). ACCEPT_WITH_CHANGES; fixes applied (preflight false-pass,
  add-dir egress, cost claim, qwen preflight, independence caveat).
- Evidence: preflight fable=UNAVAILABLE, opus=OK; skill enabled.

## PHASE B — DONE (verified)
- Skill leon-critic-loop (enabled) + scripts/critic-loop.sh (one command: preflight ->
  scoped read-only review -> standardized report with verdict/cost/session).
- Dogfood: ran on a scoped copy of the feedback report -> auto Fable->Opus fallback ->
  VERDICT=ACCEPT_WITH_CHANGES, report at reports/critic-loop-phaseB-selftest.md, cost ~$0.51.

## PHASE D — DONE (verified)
- Skill leon-ops-preflight (enabled) + 2 scripts:
  * capability-preflight.sh — OK/MISSING/DOWN/DEGRADED for claude(opus+fable)/web/browser/qwen/git.
    Live result correctly flagged web/extract DEGRADED (search-only backend) and qwen DOWN.
  * annotate-bg-event.py — classifies exit codes vs intent: NORMAL/INTENTIONAL-STOP/NEEDS-LOOK/FAILURE.
    Verified on this session's real 143 (CIE kill) and llama exit-1 (server stop) + pytest exit-1 (FAILURE).

## What is now automatic that used to be manual
- Model routing (no more "use opus"/"ask fable" every turn) — SOUL + skill.
- The independent-review pass — one command (leon-critic-loop).
- Capability gaps caught up front; background exit codes arrive interpreted.

## Config (unchanged so far; write_approval stays OFF until C6 to avoid gating my own build)
memory.write_approval=False, skills.write_approval=False, approvals.mode=smart,
approvals.cron_mode=deny, tirith_enabled=True, tirith_fail_open=True (-> flip false for headless C4).

## PHASE C0 — DONE (verified) — GATE OPEN, awaiting Leon decision
Built (TDD): src/leon_pattern_miner/cie_recall.py (scorer + stratified sampler),
tests/test_cie_recall.py (6 tests, green), scripts/run_cie_recall_gate.py (reuses
run_cie_harness with an Opus chat_func so Opus sees identical prompt/windows/validator).
Ran Opus over a 15-session stratified gold sample (33 windows, 51 gold records, 0 errors).

RESULT (small-sample, directional): local Qwen recall vs Opus = 0.35 (quote-strict 0.29),
agreement/precision 0.62, session coverage 15/15 Opus vs 12/15 Qwen.
Per-bucket recall: short 0.50, medium 0.40, long 0.28 -> loss worst in long sessions,
which hold most findings. => existing CIE corpus is LOW-RECALL (under-collects).

Self-reviewed via leon-critic-loop (Opus): ACCEPT_WITH_CHANGES; all 5 corrections applied
(per-bucket breakdown computed not asserted; precision relabeled as agreement not correctness;
disclosed code-level leniency = upper bound; flagged unsourced spend; verified Qwen used
3500-token windows so "same windows" holds). Report: reports/cie-c0-recall-gate-2026-06-13.md.
Critic report: reports/critic-loop-c0-recall-gate.md. Tests: 49 passed.

DECISION NEEDED (C0 gate): recommend OPTION 2 (hybrid) — Opus re-extract MEDIUM+LONG
sessions, keep Qwen for SHORT, then build C1-C6 on the recovered corpus; sensitive records
adjudicated local-only. Will compute concrete $/call + total before any full Opus run.
Alternatives: Option 1 full Opus re-extract; Option 3 tune local first.
Default if Leon does not object: OPTION 2.

## REMAINING
- [ ] PHASE C1..C6 — on the (re-extracted) corpus per the C0 decision
- [ ] PHASE E — pluggable memory evaluation

## INCIDENT / DRIFT CORRECTION (2026-06-15): Grok tested through wrong harness
- What happened: Grok 4.3 none/low/high reasoning samples were run through `miner extract --use-llm`, which calls legacy `llm_extractors.py` and sends keyword-selected/truncated turns.
- Why it matters: those runs measured the legacy session extractor/provider path, not Grok's ability to perform the CIE north-star task.
- Correction: treat the Grok sample reports as provider/path smoke evidence only. The next valid Grok quality test is the frozen CIE benchmark with full CIE prompt/windowing/codebook/few-shots/validator/scorer.
- Context guardrails added: `AGENTS.md`, `docs/status/current-state.md`, updated `README.md`, updated Grok status docs.

## SIDE DELIVERABLE (2026-06-13): Frozen benchmark dataset + roadmap
- Historical private work froze the C0 Opus-vs-Qwen data into a durable local benchmark.
  Public-repo update: the checked-in `benchmark/cie-extraction-v0/` payload is now a
  synthetic fixture with the same 15-session / 287-turn / 51-reference-finding shape and
  preserved Qwen-baseline score metrics. Raw conversation-derived files stay local/ignored.
- Integrity verified: all 62 gold evidence refs point to real turns (0 dangling).
- Roadmap for a coding agent to build the scoring harness:
  docs/plans/cie-extraction-benchmark-roadmap-2026-06-13.md.
  Opus-reviewed via leon-critic-loop -> ACCEPT_WITH_CHANGES; all 7 corrections applied
  (Wilson CIs, GPU non-determinism -> --runs 3 mean±sd, circularity banner,
  precision->agreement relabel, greedy-match warning, paired model-vs-model scoring,
  optional skill deps + record schema). Critic report: reports/critic-loop-bench-roadmap.md.
- Sizing answer: 15 sessions/51 findings = directional smoke (CI ±0.13); ~150 gold
  findings (~45-60 sessions) for a reliable headline; ~300-400 for per-bucket credibility.
- Tests: 49 passed.
