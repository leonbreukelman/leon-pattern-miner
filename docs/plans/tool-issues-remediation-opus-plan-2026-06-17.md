# Implementation Plan — Validated Tool-Issue Remediation
**Date:** 2026-06-17 · **Reviewer:** Opus (independent planning) · **Scope-locked to evidence in** `leon-pattern-miner/reports/tool-issue-validation-2026-06-17.md`

---

## 0. Operating constraints (binding on every slice)

- **No live API/model/corpus calls** in any test or verification step. All tests use fixtures/stubs/recorded artifacts.
- **No raw transcript DB content** leaves the machine. Cloud review (if any) receives only this evidence summary and sanitized local reports.
- **No new core model-tool or toolset.** Guardrails ship as **plugins/hooks** only. Skills require explicit Leon sign-off before authoring.
- **Fail-closed review semantics:** any review whose output is truncated by max-turns, errored, or permission-denied is treated as **incomplete**, never as "pass."
- **Worktrees are dirty.** No `git reset --hard`, no `git checkout -- .`, no `git clean`. Build on existing dirty/untracked work; verify before duplicating.

---

## 1. Triage table

| # | Issue | Enough data? | Target repo | Action | Exact evidence |
|---|-------|:---:|---|---|---|
| 1 | Terminal tool-thrash / blind retry loops | Yes | hermes | **Verify-existing** (`plugins/tool-thrash-guard/`) | Validation report #1; untracked `plugins/tool-thrash-guard/` + `tests/plugins/test_tool_thrash_guard_plugin.py` |
| 2 | Missing module/env/path/permission recovery class | Yes | hermes | **Implement-now** (extend thrash-guard diagnosis OR watchdog annotation) | Validation report #2 |
| 3 | Tool/review cap fallback (max-turn / missing-python) | Yes | hermes | **Verify-existing + harden** (`scripts/model_review.py`, `review_artifact.py`) | Validation report #3; untracked `scripts/model_review.py`, `scripts/review_artifact.py`, `tests/test_model_review.py` |
| 4 | Model/tool provenance misreport | Yes | hermes | **Verify-existing + harden** (require command/runtime proof) | Validation report #4; `M tests/test_model_tools.py` |
| 5 | Grok 4.3 invalid diff output | **No (partial)** | — | **Defer** — mark suspected/unvalidated; no implementation | Validation report #5 |
| 6 | CIE combined run: 95 errored windows | Yes | leon-pattern-miner | **Implement-now** (rerun/resume strategy, no live run) | Validation report #6 |
| 7 | 1,352 rejected CIE records lack persisted cause | Yes | leon-pattern-miner | **Implement-now** (`cie_rejections` table) | Validation report #7; current fact: only `cie_window_runs.records_rejected` exists |
| 8 | `no_signal_windows: 0` in combined run | Yes (interpretation) | leon-pattern-miner | **Implement-now** (reporting/pass-strategy disclosure) | Validation report #8; combined mode returns family-all per window |
| 9 | Legacy `extract --use-llm` Grok samples | Yes (historical) | leon-pattern-miner | **Verify-existing** (legacy retirement in dirty tree) | Validation report #9; `D src/.../llm_extractors.py`, `?? docs/plans/legacy-llm-retirement-2026-06-17.md`, `?? tests/test_legacy_llm_retirement.py` |
| 10 | xAI/Grok hidden reasoning when `reasoning_effort` omitted | Yes (historical) | leon-pattern-miner | **Verify-existing** (default now explicit `low`) | Validation report #10 |
| 11 | xAI CLI model default footgun | Yes (historical) | leon-pattern-miner | **Verify-existing** (default now `grok-4.3`) | Validation report #11 |
| 12 | Tool-output reliability drift | Resolved | leon-pattern-miner | **Verify-only (regression)** (`source_reliability=A` requires Leon/system evidence) | Validation report #12; tests exist |
| 13 | Prompt-visible quote vs raw mismatch | Resolved | leon-pattern-miner | **Verify-only (regression)** (`CIEPromptBundle.quote_sources`) | Validation report #13 |

**Net new implementation:** issues #2, #6, #7, #8.
**Verify-then-harden (do not rewrite):** #1, #3, #4, #9, #10, #11.
**Regression-pin only:** #12, #13.
**Defer:** #5.

---

## 2. Pre-flight: verify existing dirty work before writing anything

Run these **read-only/test-only** checks first. They decide whether a slice is "verify" or "implement." Do **not** modify files in this phase.

**Hermes** (`<hermes-repo>`):
```bash
cd <hermes-repo>
git status --porcelain
ls -R plugins/tool-thrash-guard plugins/tool-failure-watchdog
python -m pytest tests/plugins/test_tool_thrash_guard_plugin.py \
                 tests/plugins/test_tool_failure_watchdog_plugin.py \
                 tests/test_model_review.py tests/test_model_tools.py -v
```

**Pattern-miner** (`<pattern-miner-repo>`):
```bash
cd <pattern-miner-repo>
git status --porcelain
grep -rn "no_signal_windows\|records_rejected\|validate_cie_payload\|init_cie_tables\|cie_window_runs\|cie_records" src/
python -m pytest tests/test_legacy_llm_retirement.py tests/test_core_pipeline.py -v
```

**Decision rule (fail-closed):** a verify-existing slice is only marked "verified" if its tests **exist, assert the target invariant, and pass on the dirty tree**. If a test is missing, red, or green-but-unrelated, the slice converts to **implement-now** and gets a RED test added first. If a slice's stated file does not contain the symbol it claims to edit, the slice is blocked until retargeted; do not implement against the wrong file.

---

## 3. Hermes slices

### Slice H1 — Verify & pin tool-thrash-guard (Issue #1)
**Goal:** confirm the existing plugin blocks an immediate same-session, same-path patch retry after a stale/context patch failure, and that the block clears on `read_file` / `write_file` / successful patch.

- **Files:** `plugins/tool-thrash-guard/` (read only), `tests/plugins/test_tool_thrash_guard_plugin.py`.
- **RED (only if missing):** add cases asserting (a) second identical failed-patch attempt on same path is blocked; (b) intervening `read_file` clears the block; (c) successful patch clears the block; (d) different-path patch is **not** blocked.
- **GREEN:** no new code if existing plugin already passes; otherwise fill the gap surfaced by the RED test. **Do not duplicate** — extend the existing untracked plugin.
- **Verify:** `python -m pytest tests/plugins/test_tool_thrash_guard_plugin.py -v`

### Slice H2 — Recovery-class diagnosis before retry (Issue #2) — *implement-now*
**Goal:** classify a failed tool call into `module_missing | path_missing | permission_denied | env_missing | other` and emit an **advisory annotation** (watchdog) and/or feed the thrash-guard so a blind retry is discouraged with a concrete next-step hint.

- **Decision:** keep it in **`plugins/tool-failure-watchdog/`** as advisory-only (no `pre_tool_call`, no new tools/toolsets) per existing artifact intent. Thrash-guard remains the only blocker (path-based); watchdog only annotates with the diagnosed class + suggested recovery.
- **Files:** `plugins/tool-failure-watchdog/` (add a `classify_failure` helper + annotation text), `tests/plugins/test_tool_failure_watchdog_plugin.py`.
- **RED tests:** feed canned failure payloads (e.g. `ModuleNotFoundError: No module named 'foo'`, `No such file or directory`, `Permission denied`, `command not found: python`) and assert the annotation names the correct class and a recovery hint (install/verify-path/check-perms/select-interpreter). Assert watchdog adds **no** `pre_tool_call` and registers **no** tool.
- **GREEN:** implement `classify_failure` as pure string/regex matching over recorded stderr fixtures; wire into the existing post-failure annotation path.
- **Verify:** `python -m pytest tests/plugins/test_tool_failure_watchdog_plugin.py -v`

### Slice H3 — Review helper fail-closed hardening (Issue #3) — *verify + harden*
**Goal:** `model_review.py` / `review_artifact.py` must **narrow/resume** rather than silently skip on max-turn or missing-python, and must fail closed on invalid Claude JSON.

- **Files:** `scripts/model_review.py`, `scripts/review_artifact.py`, `tests/test_model_review.py`.
- **RED tests (no network):** stub the model call layer.
  - max-turns/truncated output ⇒ result status is `incomplete`, exit non-zero, message advises narrow/resume — **never** `pass`.
  - missing python interpreter ⇒ classified failure (ties to H2 class), `incomplete`, non-zero.
  - invalid/non-parseable Claude JSON ⇒ fail closed (`incomplete`), non-zero.
  - permission-denied ⇒ `incomplete`.
- **GREEN:** add/confirm the fail-closed branch and a `--resume`/`--narrow` affordance that re-scopes input rather than dropping it. Reuse existing preflight for Opus/Fable; **do not** add a new model path.
- **Verify:** `python -m pytest tests/test_model_review.py -v`

### Slice H4 — Provenance proof requirement (Issue #4) — *verify + harden*
**Goal:** assistant-emitted model/tool labels must be backed by runtime metadata (command/runtime proof); contradiction ⇒ flagged, not trusted.

- **Files:** `tests/test_model_tools.py` (modified), plus the provenance-assertion helper it exercises (locate via `git status` + the test imports; do not invent a new module).
- **RED tests:** given a claimed label that contradicts captured runtime metadata, assert the helper rejects/flags it; given matching command+runtime proof, assert it accepts.
- **GREEN:** implement/confirm the proof check in the existing helper. Keep schema narrow — no new core tool.
- **Verify:** `python -m pytest tests/test_model_tools.py -v`

### Slice H5 — Issue #5 (Grok invalid diff): **do not implement**
- Add a single line to the Hermes status artifact / triage doc: *"Grok 4.3 invalid-diff: suspected, unvalidated — needs raw artifact + reproduction before any guard."* No code, no test, no plugin behavior.

---

## 4. leon-pattern-miner slices

### Slice P1 — Persist CIE rejection causes (`cie_rejections` table) (Issue #7) — *implement-now*
**Goal:** every rejected CIE record gets a row with its cause; `cie_window_runs.records_rejected` count must reconcile with `cie_rejections` row count per window.

- **Files:** `src/leon_pattern_miner/cie.py` (schema + insert helper + run harness call site), `tests/test_cie_rejections.py` or focused additions to `tests/test_cie_harness.py`. Do **not** put this in `db.py`/`runner.py`; `init_cie_tables()`, `validate_cie_payload()`, and `run_cie_harness()` are in `cie.py`.
- **Schema:** `cie_rejections(id, window_id, session_id, extractor_version, family, rejection_cause, record_json, prompt_hash, created_at)`; add idempotent `CREATE TABLE IF NOT EXISTS` inside `init_cie_tables()` alongside `cie_window_runs`/`cie_records`.
- **RED tests:** run the in-memory/SQLite CIE harness over a fixture window that produces known rejections; assert (a) one `cie_rejections` row per rejected record, (b) `cie_window_runs.records_rejected` equals `count(cie_rejections WHERE window_id/extractor_version/family match)`, (c) `rejection_cause` is from the validator's closed set, and (d) rejected rows are not duplicated as accepted `cie_records`.
- **GREEN:** emit rejection rows at the `validate_cie_payload()` → `run_cie_harness()` consumption site in `cie.py` after the `rejected` list is known. No live corpus; use local fixtures.
- **Verify:** `python -m pytest tests/test_cie_rejections.py -v` (or the targeted node in `test_core_pipeline.py`)

### Slice P2 — Combined-pass signal-coverage disclosure (Issue #8) — *implement-now*
**Goal:** stop reporting `no_signal_windows: 0` as a coverage claim when combined mode returns family-all by construction. Expose the **pass strategy** instead.

- **Locate first:** grep for the real `no_signal_windows` emission path before editing. `report.py` is the old pilot report over the `records` table; do not patch it unless the grep proves it renders CIE run summaries.
- **Likely files:** `src/leon_pattern_miner/cie.py` if the summary object needs a `pass_strategy`/`no_signal_diagnostic` field, plus `docs/status/current-state.md` or dated report/status generation code that actually prints CIE summaries.
- **RED tests:** for a combined-mode CIE summary/report function at the real emission site, assert the output includes an explicit `pass_strategy: combined` / `signal_triage: not_diagnostic_for_combined` (or equivalent) and that `no_signal_windows=0` is not presented as coverage evidence; for per-family mode, assert `no_signal_windows` is still diagnostic.
- **GREEN:** branch the real summary/report rendering on pass mode; add the disclosure field; update the status doc wording. If no active CIE report emitter exists in code, patch the active status/report artifact only and record that historical reports are audit-only.
- **Verify:** `python -m pytest tests/test_core_pipeline.py -k 'report or combined or signal' -v`

### Slice P3 — Errored-window rerun/resume strategy (Issue #6) — *implement-now, offline only*
**Goal:** give the 95-errored-window case a deterministic **resume** path (select windows with error status and re-enqueue) **without** running any live corpus.

- **Files:** `src/leon_pattern_miner/cie.py` (pure selector over `cie_window_runs.status`), `tests/test_resume_errored.py` or `tests/test_cie_harness.py`.
- **RED tests:** seed a fixture DB with mixed `processed`/`error` window rows and assert the selector returns exactly the errored window ids/families for an extractor version. Assert no-op when zero errored windows.
- **GREEN:** ship only the pure selector + tests in this pass. Defer `cli.py --resume-errored` and runner subset-plumbing because `cli.py` is already dirty and the real 95-window rerun is Leon-gated/deferred.
- **Verify:** `python -m pytest tests/test_resume_errored.py -v`
- **Note in slice output:** actual reprocessing of the real 95 windows is a **manual, Leon-gated** run later — out of scope for this plan's execution.

### Slice P4 — Legacy LLM retirement regression pin (Issues #9, #10, #11) — *verify-existing*
**Goal:** confirm the dirty-tree retirement holds: `extract --use-llm` legacy path gone/guarded; xAI default `reasoning_effort=low` (explicit), `none` only when explicitly passed; xAI CLI model defaults to `grok-4.3`.

- **Files:** `tests/test_legacy_llm_retirement.py` (verify it covers all three; add missing cases), `src/leon_pattern_miner/cli.py` (read-only confirm defaults).
- **RED (only if uncovered):** assert (#9) the retired extractor import path is absent/guarded and `--use-llm` legacy session extractor errors or routes to the supported path; (#10) omitting `reasoning_effort` yields explicit `low`, and `none` only with explicit flag; (#11) xAI model default resolves to `grok-4.3`.
- **GREEN:** no new behavior expected — fill only gaps the RED cases expose, against the existing dirty implementation. **Do not re-delete or re-edit retirement files that are already staged-as-deleted.**
- **Verify:** `python -m pytest tests/test_legacy_llm_retirement.py -v`

### Slice P5 — Resolved-issue regression pins (Issues #12, #13) — *verify-only*
**Goal:** lock in the two already-resolved behaviors so they can't silently regress.

- **Files:** existing tests covering `source_reliability` and `CIEPromptBundle.quote_sources` (locate via grep; do not duplicate).
- **Action:** run them; if either lacks an explicit assertion that (#12) tool-only evidence cannot reach `source_reliability=A` without Leon/system evidence, or (#13) prompt-visible quote sources are validated against raw sources, add one focused assertion. No implementation changes.
- **Verify:** `python -m pytest -k 'reliability or quote_source or prompt_bundle' -v`

---

## 5. Execution order (sized for local test runs)

1. **Pre-flight (§2)** — establish which slices are verify vs implement.
2. **P5** (regression pins) — cheapest, locks resolved ground truth.
3. **P4** (legacy retirement verify) — confirms dirty-tree assumptions before building on them.
4. **P1** (`cie_rejections`) — schema foundation; P3 depends on window-status reads.
5. **P3** (resume-errored) — builds on P1's status model.
6. **P2** (combined-pass disclosure) — independent reporting change.
7. **H1** (thrash-guard verify) → **H3** (review fail-closed) → **H4** (provenance) → **H2** (recovery-class diagnosis).
8. **H5** (Grok #5 doc note) — last, no code.

Each slice is independently testable; commit per slice on a feature branch (worktrees are dirty — branch, do not reset).

---

## 6. Explicit "verify, do not duplicate" list

- `plugins/tool-thrash-guard/` — extend/verify; **do not** reauthor (H1).
- `plugins/tool-failure-watchdog/` — extend the existing advisory plugin for H2; **do not** create a second watchdog.
- `scripts/model_review.py`, `scripts/review_artifact.py` — harden existing fail-closed logic (H3); **do not** add a new review entrypoint.
- Provenance helper behind `tests/test_model_tools.py` — extend (H4); **do not** add a new core tool.
- Legacy-retirement work in the pattern-miner dirty tree (`D llm_extractors.py`, `docs/plans/legacy-llm-retirement-2026-06-17.md`, `tests/test_legacy_llm_retirement.py`) — verify (P4); **do not** re-delete or re-stage.
- `source_reliability` and `CIEPromptBundle.quote_sources` tests — pin only (P5).

---

## 7. Explicit "do NOT touch"

- **No fix/guard/test for Issue #5** (Grok invalid diff) beyond the one-line "suspected/unvalidated" note (H5).
- **No new core model-tool or toolset** for any guardrail.
- **No `pre_tool_call` hook** in the watchdog (advisory annotation only); blocking stays confined to the existing path-scoped thrash-guard.
- **No live API/model/corpus runs** — including the real 95-window rerun (P3 ships the mechanism only; the run is Leon-gated, later).
- **No raw transcript DB egress** to cloud review.
- **No destructive git** (`reset --hard`, `checkout -- .`, `clean`) on either dirty worktree.
- **No new skills** without explicit Leon sign-off.

---

## 8. Final Opus verification packet (post-implementation, fail-closed)

Deliver a single artifact containing:

1. **Per-slice test transcripts** — the exact `pytest` invocation + full output for H1–H4, P1–P5. Any run truncated by max-turns/error/permission-denied is reported as **INCOMPLETE**, not pass.
2. **Coverage reconciliation** — for P1: a query result showing `records_rejected == count(cie_rejections)` per window on the fixture DB.
3. **Negative-control evidence** — H3: proof that max-turn/missing-python/invalid-JSON each yield non-zero exit + `incomplete`; H4: proof a contradictory provenance label is rejected.
4. **No-duplication attestation** — `git diff --stat` per repo confirming the existing untracked plugins/scripts were **extended**, not replaced (no new parallel files).
5. **Constraint attestation** — explicit statements that: no network/model calls ran in tests, no transcript DB content was sent off-machine, no core tool/toolset was added, no destructive git ran, Issue #5 received only the suspected/unvalidated note.
6. **Deferred-work register** — Issue #5 reproduction requirement, and the Leon-gated real 95-window rerun (P3), listed as open items with preconditions.
7. **Fail-closed verdict line** — overall status is `PASS` only if every slice's tests are present and green and all constraint attestations hold; otherwise `INCOMPLETE` with the blocking items enumerated.
