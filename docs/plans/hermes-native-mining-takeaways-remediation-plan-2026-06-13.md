# Hermes-Native Mining Takeaways Remediation Implementation Plan

**Companion to:** `docs/specs/hermes-native-mining-takeaways-remediation-spec-2026-06-13.md`
**Date:** 2026-06-13
**Principle:** built-in/Hermes-supported first; TDD-first; promote nothing without Leon sign-off; no Hermes core edits on the default path.

This plan is written to be executed in a **fresh session** without re-deriving strategy. Two work tracks:
- **Track H** — Hermes user-space (behavioral takeaways 1–7): `~/.hermes/*` + `config.yaml`. No edits to the `$HOME/hermes` source tree.
- **Track M** — leon-pattern-miner (takeaway 8 CIE governance): `$HOME/projects/leon-pattern-miner`.

Repos:
- Hermes config/user-space root: `~/.hermes/` (settings authority). Hermes **source** (fallback only): `$HOME/hermes/`.
- Miner repo: `$HOME/projects/leon-pattern-miner/`.

> Use supported Hermes config/inspection commands for `config.yaml` and hooks changes: `hermes config set ...`, `hermes config edit`, `hermes hooks list`, `hermes hooks test`, and `hermes hooks doctor`. Avoid hand-editing `~/.hermes/config.yaml` unless a supported command cannot express the change.

---

## Recommended first slice (do this before anything else)

**Slice 0 — Lock the provisional boundary + ship the #1 guardrail (model-claim provenance).** Highest leverage, lowest risk, and it is the expedition's own #1 finding.

1. **Track H:** turn on the promotion floor and write the provenance doctrine.
   - `config.yaml`: `memory.write_approval: true`, `skills.write_approval: true`, `approvals.mode: manual`, `approvals.cron_mode: deny`, `security.tirith_enabled: true`.
   - `~/.hermes/SOUL.md`: add the standing provenance rule ("never state a model did work without the invocation/runtime metadata; 'Fable' means `--model fable`").
   - `~/.hermes/skills/hermes-ops/model-claim-provenance/SKILL.md`.
   - `~/.hermes/agent-hooks/provenance-lint.sh` wired as a `transform_llm_output` shell hook; verify with `hermes hooks test transform_llm_output`.
2. **Track M (cheap, unblocks all of takeaway 8):** persist CIE rejected records.
   - Source-check `rec:a7e576c0…` via local `session_search`/`turns.text` and record the verdict in the miner (do **not** promote).

**Why first:** it makes the corpus safe-by-default (nothing can auto-promote), ships the single highest-value product guardrail on a verified principle, and turns the 1352 silent rejections into analyzable data — the prerequisite for the rest of the governance work.

---

## Track H — Hermes behavioral guardrails (no core edits)

### Phase H1 — Promotion floor + standing doctrine (SOUL)
**Paths:** `~/.hermes/config.yaml`, `~/.hermes/SOUL.md`
**Tasks**
1. Set `memory.write_approval: true`, `skills.write_approval: true`, `approvals.mode: manual`, `approvals.cron_mode: deny`, `security.tirith_enabled: true`. (Optional: `checkpoints.enabled: true`.)
2. Append SOUL doctrine blocks: provenance (1), no-echo (2), gate ladder (4), autonomy bounds (5), stuck-state escape (7). Keep concise; SOUL is capped at 20k chars and is the stable prompt tier.
**Verify**
- `hermes config show | grep -E 'write_approval|approvals|tirith'`
- Stage a dummy agent memory write → confirm it lands in `/memory pending` (not written).
**Artifacts:** updated `config.yaml`, `SOUL.md`.

### Phase H2 — Skills bundle (TDD-light: author + load test)
**Paths:** `~/.hermes/skills/hermes-ops/{model-claim-provenance,tool-thrash-recovery,expected-vs-actual-debugging,gate-ladder,autonomy-boundaries,stuck-state-escape}/SKILL.md`
**Tasks**
1. Write each SKILL.md with `When to Use / Procedure / Pitfalls / Verification` per `work-with-skills.md`. Mark debugging + thrash-recovery as rigid procedures.
2. `expected-vs-actual-debugging`: require expected/actual/delta/fix/verify before complex fixes.
3. `tool-thrash-recovery`: env preflight (cwd, interpreter, package manager, PATH, key modules) after a missing-binary/module/arg/env error.
**Verify**
- `hermes skills list | grep -E 'provenance|thrash|expected-vs-actual|gate-ladder|autonomy|stuck-state'`
- `hermes chat -q "/expected-vs-actual-debugging help"` loads cleanly.
**Artifacts:** six SKILL.md files.

### Phase H3 — Guardrails plugin (stateful) + shell-hook equivalents (TDD-first)
**Paths:** `~/.hermes/plugins/hermes-guardrails/{plugin.yaml,guardrails.py}`, `~/.hermes/agent-hooks/{provenance-lint.sh,gate-ladder.sh}`, `~/.hermes/config.yaml` (`hooks:` block), plus a local pytest for `guardrails.py`.
**TDD tasks (write tests first)**
1. **Provenance detector** (takeaway 1): unit test — response with model claim + no command → flagged; with command → passes. Implement as `transform_llm_output`.
2. **Echo detector** (2): unit test — high-similarity-to-user + no tool call → detected; substantive turn → passes. Implement as `post_llm_call`/`transform_llm_output` with per-session state.
3. **Tool-thrash streak** (3): unit test — N same-class failures → `pre_tool_call` returns `{"action":"block",...}`; mixed/低 streak → no block. Implement `post_tool_call` classifier + `pre_tool_call` gate, per-session counters.
4. **Gate-ladder matcher** (4): `gate-ladder.sh` blocks deploy/prod/irreversible patterns lacking an approval marker; unit/`hermes hooks test pre_tool_call --for-tool terminal`.
5. **Stuck-state** (7): reuse echo repeated-turn signal to inject escape guidance via `pre_llm_call`.
6. Register all in `config.yaml hooks:` (shell) and/or `plugin.yaml` (plugin); consent via `hermes hooks` / `HERMES_ACCEPT_HOOKS=1` for non-TTY.
**Verify**
- `pytest` on the plugin test module (run with the plugin dir on `PYTHONPATH`).
- `hermes hooks doctor` (exec bit, allowlist, JSON validity, mtime).
- `hermes hooks test transform_llm_output` / `... pre_tool_call --for-tool terminal`.
**Artifacts:** plugin + shell hooks + hook test report.

### Phase H4 — Gate ladder hardening (built-ins)
**Paths:** `~/.hermes/config.yaml`
**Tasks**
1. Confirm Tirith active and (optionally) `security.tirith_fail_open: false` for high-security.
2. Curate `command_allowlist` (audit, don't broaden).
3. Confirm `approvals.cron_mode: deny`. Document the ladder in `gate-ladder/SKILL.md` + SOUL.
**Verify:** `hermes doctor`; trigger a dangerous command → approval prompt appears; cron job hitting a dangerous command is denied.

> **Core-edit fallback (only if Leon wants the floor changed for everyone):** add patterns to `$HOME/hermes/tools/approval.py` (`DANGEROUS_PATTERNS` / `UNRECOVERABLE_BLOCKLIST`); tests in `$HOME/hermes/tests/` (approval test module). Upstreaming echo/thrash as built-ins would touch `$HOME/hermes/run_agent.py` (turn lifecycle) + `$HOME/hermes/model_tools.py` (`handle_function_call`) with tests beside the agent-loop suite. **Not on the default path.**

---

## Track M — CIE governance (leon-pattern-miner)

All paths under `$HOME/projects/leon-pattern-miner/`. TDD-first: add cases to `tests/test_cie_governance.py` (new) before implementing `src/leon_pattern_miner/cie_governance.py`.

### Phase M0 — Persist rejected records (first-slice dependency)
**Paths:** `src/leon_pattern_miner/cie.py` (`validate_cie_payload`, `run_cie_harness`, `init_cie_tables`), `tests/test_cie_harness.py`
**Tasks (tests first)**
1. New table `cie_rejections(record_id, session_id, window_id, extractor_version, codebook_code, statement, cause, detail, created_at)` in `init_cie_tables`.
2. Change `validate_cie_payload`/`_reject` to return structured causes; have `run_cie_harness` persist each rejection with a cause enum (`quote_not_found|code_not_in_codebook|missing_field|invalid_json|source_grade_incompatible|other`).
3. Backfill helper to re-derive rejections for the existing run if cheap; else mark forward-only.
**Verify:** `pytest tests/test_cie_harness.py -q`; rejection counts reconcile to ~1352.
**Artifacts:** migration + `reports/cie-rejection-causes-2026-06-13.md`.

### Phase M1 — Governance schema + module skeleton
**Paths:** `src/leon_pattern_miner/cie.py`/`db.py`, `src/leon_pattern_miner/cie_governance.py` (new), `tests/test_cie_governance.py` (new)
**Tasks**
1. Add tables (from doctrine §C, scoped): `cie_adjudications`, `cie_clusters`, `cie_promotion_queue`; add `promotion_state` default `'candidate'`, `cluster_id`, `corroboration_count` to `cie_records` (additive).
2. Stub `adjudicate()`, `cluster()`, `triage_rejections()`, `reconcile_sensitivity()`, `rerun_failed_windows()`, `build_promotion_queue()` with signatures + tests for table init and idempotency.
**Verify:** `pytest tests/test_cie_governance.py -q`.

### Phase M2 — Rejection triage + sensitivity reconciliation (analysis, no LLM)
**Tasks (tests first)**
1. `triage_rejections()` → cause breakdown (counts + %), split hallucinated-quote vs format/codebook.
2. `reconcile_sensitivity()` → Qwen (11.2% personal / 1 secret) vs CIE (0.23% personal / 8 secret) divergence; recommend gating classifier (default: stricter/both).
**Verify:** `pytest -q`; emit `reports/cie-sensitivity-reconciliation-2026-06-13.md`.

### Phase M3 — Clustering / dedup (local embeddings)
**Tasks (tests first)**
1. `cluster()` over a high-count code first (`authorization_limit` 674) using local embeddings (BGE-M3 or Qwen3-Embedding-0.6B per doctrine §F) + exact-code match; write `cie_clusters`, set `cluster_id`/`corroboration_count`.
2. Report distinct-insight-to-record ratio.
**Verify:** `pytest -q`; `reports/cie-clustering-2026-06-13.md`. (Tests use a fixture embedder to stay offline/deterministic.)

### Phase M4 — Frontier adjudication (orchestrated via Hermes natives)
**Tasks (tests first)**
1. `adjudicate(records)` writes `cie_adjudications` (verdict, code_agreement, source_grade_agreement, rationale). Adjudicate all steering/secret/personal + a stratified ~10–20% sample, plus ~20 of the persisted rejections.
2. **Orchestration:** run via Hermes **delegation** (parallel subagents over batches; `subagent_stop` audit) or a **cron LLM job** (self-contained prompt, `--deliver local`, `[SILENT]` when idle). Keep raw transcripts local; subscription-first model routing.
3. Produce first real precision estimate for the CIE run.
**Verify:** `pytest -q` (mocked adjudicator in tests); `reports/cie-adjudication-2026-06-13.md`. Cost anchor for a full Opus extraction alternative: ~$141 (1503 × $0.094) — record but do not run here.

### Phase M5 — Rerun failed long windows
**Tasks (tests first)**
1. `rerun_failed_windows()` reads `cie_window_runs WHERE status='error'` (95: 56 malformed JSON, 39 context-overflow), re-windows smaller with output caps, reprocesses.
2. Report residual overflow (target < 39) and new records.
**Orchestration:** Hermes **cron** job over the local DB.
**Verify:** `pytest -q`; `reports/cie-failed-window-rerun-2026-06-13.md`.

### Phase M6 — Promotion queue + Leon sign-off
**Tasks (tests first)**
1. `build_promotion_queue()` inserts only candidates passing: `quote_verified` ∧ adjudication=pass ∧ source-grade threshold ∧ corroboration threshold (except explicit one-offs) ∧ no unresolved contradiction. Include `evidence_brief`, `risk_brief`, `proposed_sink`.
2. Sign-off path: present queue to Leon; on approval, the corresponding Hermes write is staged through `/memory pending` / `/skills pending` (Phase H1 gate). Secret/personal never auto-queued.
**Verify:** `pytest -q`; `reports/cie-promotion-queue-2026-06-13.md`. End-to-end check: an approved item only lands after Leon `/memory approve`.

---

## Verification commands (consolidated)
**Track H**
```
hermes config show | grep -E 'write_approval|approvals|tirith|checkpoints'
hermes skills list
hermes hooks list && hermes hooks doctor
hermes hooks test transform_llm_output
hermes hooks test pre_tool_call --for-tool terminal
git -C $HOME/hermes status        # expect: no source changes (default path)
```
**Track M**
```
cd $HOME/projects/leon-pattern-miner
pytest -q                                # new governance tests + existing 43
pytest tests/test_cie_governance.py -q
python scripts/run_cie_governance.py --help
```

## Artifact paths
- Specs/plans (parent saves): `docs/specs/hermes-native-mining-takeaways-remediation-spec-2026-06-13.md`, `docs/plans/hermes-native-mining-takeaways-remediation-plan-2026-06-13.md`.
- Miner reports: `reports/cie-rejection-causes-2026-06-13.md`, `reports/cie-sensitivity-reconciliation-2026-06-13.md`, `reports/cie-clustering-2026-06-13.md`, `reports/cie-adjudication-2026-06-13.md`, `reports/cie-failed-window-rerun-2026-06-13.md`, `reports/cie-promotion-queue-2026-06-13.md`.
- Hermes user-space: `~/.hermes/SOUL.md`, `~/.hermes/skills/hermes-ops/*`, `~/.hermes/plugins/hermes-guardrails/*`, `~/.hermes/agent-hooks/*`, `~/.hermes/config.yaml`.

## What to defer
- **Gold-set + model bake-off + F1/precision-recall** (doctrine Phases 5–7) — depends on M2–M4 outputs; large, separate effort.
- **Fine-tuning** (triage classifier, extractor LoRA, sink classifier) — only after 500–2000 adjudicated records.
- **Full frontier extraction pass** (~$141 Opus) — decide only after M4 quantifies Qwen recall.
- **Upstreaming guardrails into Hermes core** (`tools/approval.py`, `run_agent.py`, `model_tools.py`) — only if Leon wants them shipped for all users; the user-space hook path is sufficient and reversible.
- **Triage/no-signal Stage-1** implementation (explains the implausible `no_signal_windows: 0`) — orthogonal pipeline improvement.
- **Cross-extractor old-vs-new diff** (Qwen full vs CIE) — useful but not required for governance.

---

**Both documents above are report/design only. No mined record is promoted, and the recommended path makes zero changes to the Hermes source tree.**

I've completed the read-only investigation and produced the two documents (spec + plan) above, delimited by the requested `<<<SPEC:...>>>` and `<<<PLAN:...>>>` markers for the parent agent to save.

Key conclusions for the parent:
- **No Hermes core changes are required.** All seven behavioral takeaways map cleanly onto Hermes' three-tier hook system (`pre_tool_call` block, `pre_llm_call` inject, `post_tool_call`, `transform_llm_output`), plus SOUL.md doctrine, a skills bundle, the built-in approval/Tirith/checkpoint stack, and `memory.write_approval`/`skills.write_approval` as the promotion floor. Core files (`tools/approval.py`, `run_agent.py`, `model_tools.py`) are named only as opt-in upstreaming fallbacks.
- **Takeaway 8 (CIE governance) is miner project work**, orchestrated with Hermes natives (delegation/cron) — not a platform change. The cheapest unlock is persisting the 1352 currently-counted-only rejections (`cie.py` already has the table-init and validation seams).
- **Recommended first slice:** turn on the write-approval floor + ship the model-claim-provenance guardrail (SOUL rule + skill + `transform_llm_output` shell hook), and persist CIE rejections. This makes the corpus safe-by-default and delivers the expedition's own #1 finding on a verified principle.
- The spec preserves the provisional boundary throughout: nothing is promoted without Leon sign-off through the Hermes approval gate.