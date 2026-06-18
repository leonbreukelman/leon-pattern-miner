# Legacy LLM Extractor Retirement Plan

## 1. VERDICT — Feasibility & Risk

**Feasible, low regression risk, recommend proceeding with deletion (not warning-hardening).**

The legacy path is cleanly separable. In `cli.py`, the entire deterministic extraction path (lines 159–162: `reset_stale_running_work` → `enqueue_work` → `run_deterministic_extractors`) runs **unconditionally and independently** of `--use-llm`. Every legacy behavior is gated behind `if args.use_llm:` blocks or legacy-only flags. The canonical CIE/xAI path lives entirely in `scripts/run_benchmark.py`, `adapters.py`, and `llm.py` — none of which import `llm_extractors`. So `llm_extractors.py` can be deleted and the `--use-llm` surface stripped without touching canonical code.

**Risks, all containable:**
- **DB schema coupling (medium):** `llm_session_runs` table + `errors.extractor_version` backfill + `runner.llm_progress_counts()` reference legacy data. Dropping tables on an existing `runtime/miner.db` risks a migration regression. **Default: keep schema inert (historical), remove only the active reporting surface.**
- **`cli.py` import bleed (low):** `_xai_chat_func` and several `llm.py` imports become dead after removal — must be cleaned to avoid lint/unused-import noise, but the underlying `llm.py` transport stays (canonical benchmark depends on it).
- **`tests/test_core_pipeline.py` (low):** mixes deterministic-pipeline coverage (keep) with legacy LLM coverage (remove) — needs surgical excision, not file deletion.

No public git history rewrite required. No canonical behavior change required.

---

## 2. Implementation Plan (small steps, TDD order)

**Step 0 — Inspect (read-only, before writing tests).** Read `runner.py` (`llm_progress_counts`, `status_snapshot`), `db.py` (schema for `llm_session_runs`, `errors.extractor_version`), and `tests/test_core_pipeline.py` (lines ~81, 300–599) to confirm exact legacy boundaries and whether `status_snapshot` surfaces legacy progress keys.

**Step 1 — Write failing tests (Section 3).** Commit them red.

**Step 2 — Delete `src/leon_pattern_miner/llm_extractors.py`.**

**Step 3 — Strip `cli.py`:**
- Remove import line 13 (`from .llm_extractors import ...`).
- Remove `_xai_chat_func` (66–85), `_planned_llm_count` (88–95), `LEGACY_LLM_REMOTE_SMOKE_MAX_SESSIONS` (18).
- In `cmd_extract`: delete lines 100–153 (effective-model derivation, BLOCKED guards, dry-run, live-gate) and the entire `if args.use_llm:` block (163–209). Keep 154–162 (full-corpus gate + deterministic run) and the final `print`/return.
- Remove `extract` subparser flags (263–276): `--use-llm`, `--run-purpose`, `--llm-url`, `--llm-provider`, `--llm-model`, `--llm-reasoning-effort`, `--llm-api-key-env`, `--llm-extractor-version`, `--llm-max-sessions`, `--llm-timeout`, `--llm-max-tokens`, `--dry-run`, `--confirm-live`, `--max-model-calls`. Keep `--full-corpus`, `--stale-minutes`.
- Prune now-unused `llm` imports (line 12): drop `OpenAIProviderConfig`, `ProviderCallBudget`, `chat_json_provider`, `planned_provider_call_ceiling`, `LLMHealth` if unreferenced. **Keep `health as llm_health`** (still used by `status --check-llm`).

**Step 4 — Retire active legacy reporting in `runner.py`:** if `status_snapshot` surfaces `llm_progress_counts()` keys, remove that surfacing so `status` no longer advertises the legacy path. Keep `llm_progress_counts` deletable only if no canonical caller remains; otherwise leave it defined but unwired with a `# historical/inert` comment.

**Step 5 — Excise legacy tests:** delete `tests/test_llm_provider_extractors.py`; remove the `llm_extractors`-dependent cases from `tests/test_core_pipeline.py`, preserving deterministic-pipeline coverage.

**Step 6 — Docs:** edit active docs (`AGENTS.md`, `README.md`, `benchmark/README.md`, `docs/status/current-state.md`) to remove the `--use-llm` option and its warning tape; replace with a one-line pointer to the canonical CIE path (`cie.py`, `cie_codebook.json`, `benchmark/cie-extraction-v0/`, `scripts/run_benchmark.py`). Leave dated/superseded status/plan docs untouched (historical, quarantined).

**Step 7 — Green:** `uv run pytest -q` passes; run Section 6 verification.

---

## 3. Regression Tests to Write First (and what must fail pre-impl)

Add `tests/test_legacy_llm_removed.py`:

1. **Flag gone (clean discriminator):**
   ```python
   import pytest
   from leon_pattern_miner.cli import build_parser
   def test_extract_rejects_use_llm():
       with pytest.raises(SystemExit):   # argparse "unrecognized arguments"
           build_parser().parse_args(["extract", "--use-llm"])
   ```
   *Pre-impl: flag is registered → `parse_args` succeeds → no `SystemExit` → **FAILS**.* (Asserting exit code 2 alone is insufficient — the legacy `BLOCKED` path also returns 2.)

2. **Module gone:**
   ```python
   def test_llm_extractors_module_absent():
       with pytest.raises(ModuleNotFoundError):
           import leon_pattern_miner.llm_extractors  # noqa
   ```
   *Pre-impl: module imports fine → **FAILS**.*

3. **CLI source clean of legacy symbols:**
   ```python
   from pathlib import Path
   import leon_pattern_miner.cli as cli
   def test_cli_has_no_legacy_llm_refs():
       src = Path(cli.__file__).read_text()
       for token in ("llm_extractors","run_llm_extractors","planned_llm_sessions","use_llm"):
           assert token not in src
   ```
   *Pre-impl: tokens present → **FAILS**.*

4. **Status does not advertise legacy progress** (only if Step 0 shows it currently does):
   assert `status_snapshot(conn)` keys contain no `llm_session_runs`/`local_llm` progress fields. *Pre-impl: **FAILS**.*

5. **Canonical still green (must PASS before and after — pure regression guard):** keep `tests/test_adapters.py`, `tests/test_benchmark.py`, `tests/test_provider_chat.py`, `tests/test_cie_harness.py` untouched; optionally add a smoke assert that `from leon_pattern_miner.adapters import make_xai_adapter` and `scripts/run_benchmark.py` `--adapter xai` parse succeeds.

---

## 4. Files to Delete / Modify

**Delete:**
- `src/leon_pattern_miner/llm_extractors.py`
- `tests/test_llm_provider_extractors.py`

**Modify:**
- `src/leon_pattern_miner/cli.py` (Step 3)
- `src/leon_pattern_miner/runner.py` (Step 4 — unwire `llm_progress_counts` from active reporting)
- `tests/test_core_pipeline.py` (excise legacy LLM cases; keep deterministic)
- `AGENTS.md`, `README.md`, `benchmark/README.md`, `docs/status/current-state.md` (Step 6)
- **Add:** `tests/test_legacy_llm_removed.py`

**Do NOT modify:** `llm.py`, `adapters.py`, `scripts/run_benchmark.py`, `cie.py`, `cie_codebook.json`, `benchmark/cie-extraction-v0/`, `db.py` schema, dated/historical status & plan docs.

---

## 5. Deferred to Avoid Regression

- **DB schema:** do **not** `DROP TABLE llm_session_runs` or revert the `errors.extractor_version` backfill — existing `runtime/miner.db` files hold historical rows; leave the schema inert with a `# historical — legacy LLM path retired` comment. Removing the schema is a separate, riskier migration task.
- **`status --check-llm` / `--llm-url`:** out of scope — it's a thin local-server health probe via `llm.health`, not an extraction surface, and removing it changes the `status` command contract. Keep; revisit only if it proves to be an attractor.
- **`runner.llm_progress_counts()` deletion:** unwire from active reporting now; defer outright function deletion until confirmed no caller depends on it (avoids a churny secondary change).
- **Git history:** no rewrite. Dated docs stay as historical record.
- **`llm.py` legacy-flavored helpers** (e.g. `planned_provider_call_ceiling`): keep — they're shared with the canonical benchmark budget logic.

---

## 6. Verification Checks (old path gone from active surfaces; canonical intact)

**Old path absent from active surfaces:**
```bash
test ! -f src/leon_pattern_miner/llm_extractors.py && echo "module deleted"

uv run python -c "import leon_pattern_miner.llm_extractors" 2>&1 | grep -q ModuleNotFoundError && echo "import blocked"

# argparse rejects the flag (exit 2, "unrecognized arguments")
uv run python -m leon_pattern_miner.cli extract --use-llm; echo "exit=$?"

# no live references in active code + active docs (allow only historical/quarantined dated docs)
rg -n "use-llm|run_llm_extractors|planned_llm_sessions|llm_extractors|local_llm|DEFAULT_LLM_EXTRACTOR_VERSION" \
   src/ scripts/ README.md AGENTS.md benchmark/README.md docs/status/current-state.md
# expect: zero hits (any remaining hit must be an inert db.py schema comment or a dated/quarantined doc)
```

**Canonical CIE / xAI path preserved:**
```bash
rg -n "make_xai_adapter|AdapterConfig" src/leon_pattern_miner/adapters.py        # present
rg -n "adapter.*xai|--xai-reasoning-effort|--max-model-calls|/v1/models" scripts/run_benchmark.py  # present
uv run python scripts/run_benchmark.py --help | grep -E "adapter|xai"            # flags intact

uv run pytest -q tests/test_adapters.py tests/test_benchmark.py \
   tests/test_provider_chat.py tests/test_cie_harness.py                          # green
```

**Full suite (TDD gate):**
```bash
uv run pytest -q   # all green, including new tests/test_legacy_llm_removed.py
```

Pass criteria: module/flag/import checks all confirm removal; the active-surface `rg` returns only inert-schema or dated-doc hits; every canonical check stays green.