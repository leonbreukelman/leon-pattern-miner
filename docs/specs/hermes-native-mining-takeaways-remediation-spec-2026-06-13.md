# Hermes-Native Mining Takeaways Remediation Spec

**Status:** design / report-only. No mined record is promoted to memory, skills, or tests by this spec.
**Date:** 2026-06-13
**Authors:** Opus investigation (read-only), for Leon / Hermes.
**Scope:** Turn the 2026-06-13 mining expedition takeaways into durable behavior using **Hermes built-in primitives first** (skills, hooks, SOUL/memory, approvals/Tirith/checkpoints, cron, delegation, write-approval gates, session_search), with CIE/miner project work kept separate. Core Hermes source changes are treated as a **fallback**, named explicitly but not on the default path.

---

## 1. Evidence / review inputs

| Artifact | What it establishes |
|---|---|
| `reports/mining-expedition-final-summary-2026-06-13.md` | The eight takeaways; explicit "outputs are provisional intelligence, not ready-to-promote memory"; the "what not to do yet" promotion boundary. |
| `reports/opus-review-mining-results-2026-06-13.md` | Critical correction: the "CIE v1 combined" run is **Phase 4 only** (codebook few-shots + windowing + quote validation); governance stages (source grading, adjudication, clustering, triage, promotion queue) were **not run**. Flags: 1352 un-analyzed rejections (34%), 2620 un-adjudicated records, no clustering, ~50× sensitivity divergence, 95 errored long-tail windows, implausible `no_signal_windows: 0`, single-record model-claim finding from the weakest model. |
| `reports/cie-combined-fullrun-completion-summary-2026-06-13.md` | Run counts: 1503 sessions, 2197 window runs, 2620 created / 1352 rejected, 95 errors (56 malformed JSON, 39 context-overflow), code/sensitivity/confidence distributions. |
| `reports/qwen36-fable5-results-review.md` | The seven behavioral veins (model-claim, echo, tool-thrash, operator protocol, review gates, autonomy boundaries, expected-vs-actual) with sanitized record IDs; the confirmed-and-saved facts (subscription-first routing; "Fable" = `--model fable`). |
| `reports/opus48-vs-qwen36-latest30.md` | Recall evidence: Opus 55 valid vs Qwen 12 over 30 sessions; Opus found records in 30/30 vs Qwen 10/30; Qwen found **zero** Opus missed. Low-recall signature, not high precision. Cost anchor: ~$0.094/session Opus extraction. |
| `reports/cie-v1-doctrine-implementation-plan.md` | The CIE doctrine: stages, source-appraisal grades (A–F / 1–6), units of analysis, codebook, schema additions (adjudications/clusters/promotion_queue), metrics, phases 0–7. |

Hermes capability inputs (read from `$HOME/hermes/website/docs`): `user-guide/features/hooks.md`, `guides/work-with-skills.md`, `user-guide/configuration.md`, `user-guide/security.md`, `user-guide/checkpoints-and-rollback.md`, `guides/automate-with-cron.md`, `guides/cron-script-only.md`, `guides/delegation-patterns.md`, `developer-guide/{architecture,agent-loop,tools-runtime,prompt-assembly}.md`, `user-guide/features/memory.md`.

Project code inputs (read from leon-pattern-miner): `src/leon_pattern_miner/{cie.py,db.py,runner.py,llm.py}`, `tests/test_cie_harness.py`. Confirmed: CIE persists `cie_window_runs` (incl. `status='error'`, `error_detail`) and `cie_records`; it does **not** persist rejected records, adjudications, clusters, or a promotion queue.

---

## 2. The decisive Hermes primitive: three-tier hooks

`hooks.md` establishes that Hermes already supports the exact interception points these takeaways need, in **both CLI and gateway**, with **no core edits**:

- **`pre_tool_call`** — can **block** a tool call by returning `{"action":"block","message":...}` (shell-hook form `{"decision":"block","reason":...}`). Fires in `model_tools.handle_function_call()`.
- **`pre_llm_call`** — can **inject context** into the current turn's user message (cache-safe; never mutates the system prompt). Fires once per turn in `run_agent.run_conversation()`.
- **`post_tool_call`** — observer with `result` + `duration_ms`; ideal for failure/streak tracking.
- **`transform_llm_output`** — rewrite the final response before delivery (classical code, zero extra tokens).
- **`subagent_stop`**, `pre/post_approval_request`, `on_session_*` — audit/lifecycle.

Two registration paths:
- **Shell hooks** (`hooks:` block in `config.yaml` → scripts in `~/.hermes/agent-hooks/`): drop-in, language-agnostic, subprocess-isolated, JSON wire protocol, can block and inject. Best for stateless rules.
- **Plugin hooks** (`~/.hermes/plugins/<name>/`, `ctx.register_hook(...)`): in-process Python, **keeps state across calls within a session**. Required for streak counters (tool-thrash) and similarity tracking (echo loops).

This means the behavioral takeaways (1–7) are addressable as **skills + SOUL doctrine + hooks**, which the project's own constraint ranks above core-loop changes.

---

## 3. Decision matrix: takeaway → built-in Hermes option → why → when core changes are needed

| # | Takeaway | Primary built-in option | Why this fits | Core change needed? |
|---|---|---|---|---|
| 1 | **Model-claim reliability / review-route provenance** | **SOUL.md** standing rule + **skill** `model-claim-provenance` + **plugin/shell hook** on `transform_llm_output`/`post_llm_call` that flags final responses asserting "I used/ran/reviewed with <model>" without adjacent command/runtime evidence; **session_search** to verify historical claims against `turns.text`. | The expedition's own #1 lesson and an already-confirmed fact ("Fable"=`--model fable`). Provenance is a standing behavior (SOUL) + a checkable procedure (skill) + a cheap lint (hook). session_search/FTS5 is the native way to re-derive a claim from the transcript. | **No.** Optional upstream only if Leon wants it enforced for all users (then `run_agent.py`/`transform_llm_output` default + tests). |
| 2 | **Echo / no-op loop prevention** | **Plugin hook**: on each turn compare assistant final text vs triggering user message (similarity) and whether the turn produced a tool call/artifact/decision; if high-similarity + no new action, inject a corrective via `pre_llm_call` next turn or rewrite via `transform_llm_output`. Reinforced by **SOUL** rule ("every reply must add action, artifact, decision, or synthesis"). | Mechanically detectable (the review says so). Needs cross-turn state → plugin hook, not shell. Cache-safe injection path already exists. | **No.** Upstream fallback: detector in `run_agent.py` turn lifecycle + tests. |
| 3 | **Tool-thrash recovery after repeated env/command failures** | **Plugin hook**: `post_tool_call` classifies terminal failures (missing binary/module/arg, env mismatch, timeout); after N same-class failures, `pre_tool_call` **blocks** the next blind retry with a message ordering an environment diagnosis. Paired **skill** `tool-thrash-recovery` (preflight: cwd, interpreter, package manager, PATH, key modules). | All three corpus `tool_thrash` records are real and class-detectable. Streak state → plugin hook. Blocking is a first-class `pre_tool_call` return. | **No.** Upstream fallback: `tools/terminal_tool.py` + `tools/approval.py` + tests. |
| 4 | **Review/deploy/destructive-action gate ladder** | **Built-in approval stack**: `approvals.mode: manual`, **Tirith** (`security.tirith_enabled`), **hardline blocklist**, `command_allowlist`, `approvals.cron_mode: deny`, **checkpoints** for destructive file ops. **`pre_tool_call` hook** adds project gates (block deploy/prod/irreversible targets unless an explicit approval marker is present). **SOUL** writes the ladder down (CI→merge; independent review for auth/security; human deploy authorization; preview gate for physical/destructive; human-gated stays human-gated). | Hermes ships most of the ladder; the curated `DANGEROUS_PATTERNS` covers the destructive floor. Custom gates are exactly what `pre_tool_call` matchers are for, so no curated-list edit is needed. | **No** for the recommended path. **Yes only if** Leon wants new patterns in the shipped floor: `$HOME/hermes/tools/approval.py` (`DANGEROUS_PATTERNS`, `UNRECOVERABLE_BLOCKLIST`) + tests under `$HOME/hermes/tests/` (approval tests). |
| 5 | **Autonomous work selection only inside explicit gates** | **SOUL** + **skill** `autonomy-boundaries` (may select eligible ready/backlog; must not override blocked/human-review/prod-deploy/budget/irreversible). For headless: `approvals.cron_mode: deny`. If work selection is tool-mediated (kanban), **`pre_tool_call`** blocks state transitions that bypass gates. | Autonomy is a policy, best expressed as standing doctrine + a loadable procedure; cron headless safety is already a config knob. | **No.** |
| 6 | **Expected-vs-actual debugging habit** | **Rigid skill** `expected-vs-actual-debugging` (name expected, actual, delta, fix path, verification before fixing). Optional `pre_llm_call` injection when debugging context is detected. | The densest methodology vein; "name the procedure" is the textbook skills use case (`work-with-skills.md`). | **No.** |
| 7 | **Stuck-state / impossible-protocol escape** | **SOUL** rule ("if a requested format/protocol is impossible, say so directly; do not loop or silently fail") + **skill** `stuck-state-escape`. Reuses the #2 echo plugin's repeated-turn detector to inject escape guidance. Built-in **iteration-budget pressure** already nudges consolidation. | A standing behavior + a detector already being built for #2. | **No.** |
| 8 | **CIE governance gaps** (adjudication, clustering/dedup, rejected-record triage, sensitivity reconciliation, rerun failed long windows) | **leon-pattern-miner project work** (new modules + tables), **operationalized** with Hermes natives: **delegation/subagents** or **cron LLM jobs** for frontier adjudication fan-out; **cron** to rerun the 95 errored windows smaller; **`memory.write_approval` + `skills.write_approval`** as the promotion floor; a miner `promotion_queue` requiring **Leon sign-off**. | These are analysis stages the doctrine defines but the run skipped — they belong in the miner. Hermes contributes orchestration (cron/delegation) and the promotion gate (write-approval), not the analysis logic. | **No Hermes core change.** Project changes in `src/leon_pattern_miner/` (see §4 and the Plan). |

**Net:** every takeaway has a sufficient built-in/Hermes-supported path. No Hermes core source change is required for the recommended remediation. Core files are named in the matrix only as opt-in upstreaming fallbacks.

---

## 4. Target behavior

### 4.1 Hermes side (behavioral takeaways 1–7)
- **Provenance:** Hermes never states a model performed work without the invocation/runtime metadata; reviews labeled "Fable" carry the `--model fable` command. Unverifiable historical claims are checked via `session_search` before being repeated.
- **No echo loops:** A turn that merely restates the user with no new tool call/artifact/decision/synthesis is detected and corrected.
- **Thrash recovery:** After repeated same-class command/env failures, Hermes stops blind retries and runs an environment diagnosis/preflight first.
- **Gate ladder:** Destructive/deploy/irreversible actions pass the approval stack (manual approval + Tirith + blocklist) and project `pre_tool_call` gates; human-gated work stays human-gated; cron is fail-closed.
- **Bounded autonomy:** Autonomous selection limited to clearly eligible ready/backlog work; gated states are never auto-overridden.
- **Debugging discipline:** Complex fixes are preceded by an explicit expected/actual/delta/fix/verify frame.
- **Escape behavior:** Impossible protocols are surfaced directly, not looped.

All of the above are delivered via **SOUL.md doctrine + a skills bundle + one guardrails plugin (with shell-hook equivalents)**, with **`memory.write_approval` and `skills.write_approval` turned ON** so nothing from the mining corpus becomes durable without Leon's approval.

### 4.2 Miner side (takeaway 8)
- Rejected records are **persisted** (not just counted) with a rejection cause, enabling triage.
- A governance pass produces **adjudications** (frontier verdicts), **clusters** (dedup → distinct-insight counts), a **rejection-cause breakdown**, a **sensitivity reconciliation** between Qwen and CIE, and a **rerun** of the 95 errored windows with smaller windows/output caps.
- A **promotion_queue** holds only candidates that cleared quote-verification + adjudication + source-grade/corroboration thresholds; promotion to Hermes memory/skills/tests happens **only** after Leon sign-off through the Hermes write-approval gate.

---

## 5. Non-goals
- **No platform rewrite.** No changes to the agent loop, prompt assembly, provider resolution, or tool registry on the default path.
- **No auto-promotion.** No mined record (especially the 8 CIE-secret, 6 CIE-personal, Qwen personal/quarantine, and any single model-claim record) becomes memory/skill/test without source-check + Leon sign-off.
- **No new bespoke runtime** where a Hermes primitive exists (no custom scheduler, no custom approval engine, no custom hook framework).
- **No fine-tuning / model bake-off** in this remediation (deferred to the doctrine plan's Phases 5–7).
- **No treating raw counts as findings** — clustering must precede any "N occurrences" claim.
- **No claim of detection/F1 quality** until a gold set scores it (deferred, but the matrix's #8 unblocks it).

---

## 6. Architecture / data flow

### 6.1 Hermes user-space components (no core edits)
```
~/.hermes/
├── SOUL.md                         # + standing doctrine: provenance, gate ladder,
│                                   #   autonomy bounds, no-echo, stuck-state escape
├── skills/
│   └── hermes-ops/
│       ├── model-claim-provenance/SKILL.md
│       ├── tool-thrash-recovery/SKILL.md
│       ├── expected-vs-actual-debugging/SKILL.md
│       ├── gate-ladder/SKILL.md
│       ├── autonomy-boundaries/SKILL.md
│       └── stuck-state-escape/SKILL.md
├── plugins/
│   └── hermes-guardrails/          # plugin hooks (stateful)
│       ├── plugin.yaml
│       └── guardrails.py           # register(): pre_tool_call, post_tool_call,
│                                   #   pre_llm_call, transform_llm_output, subagent_stop
├── agent-hooks/                    # shell-hook equivalents (stateless / portable)
│   ├── provenance-lint.sh          # transform_llm_output / post_llm_call
│   └── gate-ladder.sh              # pre_tool_call matcher: "terminal|write_file|patch"
└── config.yaml
    ├── hooks: { pre_tool_call, pre_llm_call, post_tool_call, transform_llm_output }
    ├── approvals: { mode: manual, cron_mode: deny, timeout: 60 }
    ├── security: { tirith_enabled: true, tirith_fail_open: false (optional) }
    ├── memory:  { write_approval: true }     # promotion floor
    ├── skills:  { write_approval: true }      # promotion floor
    └── checkpoints: { enabled: true }         # destructive-op safety net (optional)
```

Data flow for a behavioral guardrail (e.g. tool-thrash):
```
model tool_call → run_agent loop → model_tools.handle_function_call()
   → pre_tool_call (guardrails plugin): if same-class failure streak ≥ N → block w/ diagnose message
   → (else) registry.dispatch → terminal tool
   → post_tool_call (guardrails plugin): classify result, update per-session failure streak
final response → transform_llm_output (provenance-lint): flag unverified model claims
```

### 6.2 leon-pattern-miner governance components (project work)
```
src/leon_pattern_miner/
├── cie.py                # existing: windows, prompt, validate_cie_payload, run_cie_harness
│                         #   CHANGE: persist rejections (return + store cause)
├── db.py / cie.py        # CHANGE: init governance tables
│   New tables: cie_rejections, cie_adjudications, cie_clusters, cie_promotion_queue
├── cie_governance.py     # NEW: adjudicate(), cluster(), triage_rejections(),
│                         #   reconcile_sensitivity(), rerun_failed_windows()
└── ...
scripts/
├── run_cie_governance.py # NEW: CLI entrypoints for each governance pass
tests/
├── test_cie_governance.py# NEW (TDD-first)
```

Promotion data flow (provisional → durable):
```
cie_records (provisional, promotion_state='candidate')
   → cie_governance.adjudicate() [frontier via Hermes delegation/cron] → cie_adjudications
   → cie_governance.cluster() → cie_clusters (distinct-insight counts)
   → gate: quote_verified ∧ adjudication=pass ∧ source-grade/corroboration thresholds
   → cie_promotion_queue (status='proposed', evidence_brief, risk_brief)
   → Leon review → Hermes memory/skill/test write
        → BLOCKED by memory.write_approval / skills.write_approval until Leon approves
```

### 6.3 Orchestration via Hermes natives
- **Frontier adjudication / clustering** run as either a Hermes **cron LLM job** (self-contained prompt, `--deliver local`, `[SILENT]` when nothing to report) or **delegation** fan-out (parallel subagents over record batches; `subagent_stop` for audit).
- **Rerun failed windows** is a scheduled job reading `cie_window_runs WHERE status='error'`, re-windowing smaller with output caps.
- **No raw transcript leaves the box**: jobs operate on the local miner DB; provenance verification uses local `session_search`/`turns.text`.

---

## 7. Acceptance criteria

### Hermes side
1. **Provenance:** With the guardrails plugin (or `provenance-lint.sh`) active, a synthetic final response claiming "I reviewed this with Fable" **without** an adjacent command/runtime line is flagged (rewritten with a "provenance required" note) — verified via `hermes hooks test transform_llm_output` and a plugin unit test. SOUL contains the standing rule.
2. **Echo:** A crafted turn that restates the user with no tool call is detected by the plugin's similarity+action check (unit test asserts detection; non-echo turns pass through).
3. **Tool-thrash:** Simulated N same-class terminal failures cause the next blind retry to be **blocked** by `pre_tool_call` with a diagnose-first message (unit test on the streak classifier + block decision).
4. **Gate ladder:** `approvals.mode: manual` + Tirith on; a deploy/prod command without an approval marker is blocked by `gate-ladder.sh`/plugin `pre_tool_call`; `approvals.cron_mode: deny` confirmed. `hermes hooks doctor` reports the hooks healthy and consented.
5. **Autonomy / debugging / escape:** Skills load via `skill_view`/slash command; SOUL contains the autonomy bounds and stuck-state rule; `hermes skills list` shows the six skills.
6. **Promotion floor:** `memory.write_approval: true` and `skills.write_approval: true` verified — an agent memory/skill write stages for `/memory pending` / `/skills pending` instead of landing.
7. **No core diff:** `git status` in `$HOME/hermes` shows **no** source changes for the recommended path (only user-space `~/.hermes/*` and config).

### Miner side
8. **Rejections persisted:** Re-running CIE (or a backfill) populates `cie_rejections` with a cause enum (`quote_not_found` | `code_not_in_codebook` | `missing_field` | `invalid_json` | `source_grade_incompatible` | `other`); counts reconcile to the reported 1352 within tolerance.
9. **Adjudication:** `cie_governance.adjudicate()` writes `cie_adjudications` for the high-stakes subset (all steering/secret/personal + stratified sample) with verdict + rationale; a precision estimate is producible.
10. **Clustering:** `cluster()` collapses a high-count code (e.g. `authorization_limit`'s 674) into distinct-insight clusters; a distinct-insight-to-record ratio is reported.
11. **Rejection triage:** `triage_rejections()` produces the cause breakdown over the persisted rejections.
12. **Sensitivity reconciliation:** `reconcile_sensitivity()` quantifies the Qwen↔CIE divergence (Qwen 11.2% personal vs CIE 0.23%) and recommends which classifier may gate promotion.
13. **Failed-window rerun:** `rerun_failed_windows()` reprocesses the 95 errored windows with smaller windows/output caps; the residual context-overflow count is reported (target: < 39).
14. **Promotion queue:** `cie_promotion_queue` holds only gate-passing candidates; nothing is written to Hermes memory/skills without Leon sign-off (enforced by §criterion 6).
15. **Tests green:** `pytest` passes for new governance tests plus the existing 43.

---

## 8. Risks and safety boundaries
- **Promotion leakage** (highest). Mitigation: `memory.write_approval` + `skills.write_approval` ON; miner `promotion_queue` + Leon sign-off; secret/personal records never auto-queued; this spec promotes nothing.
- **Acting on a single weak-model record.** The model-claim guardrail is built on the **principle**, not on `rec:a7e576c0…`; the record is source-checked via `session_search` before being cited as proven.
- **False-positive guardrails** (blocking legitimate retries / flagging legitimate model statements). Mitigation: streak thresholds + class allowlists; hooks are non-blocking by framework guarantee except explicit `pre_tool_call` blocks, which return a message the model can act on; tune via tests before enabling in gateway; ship shell-hook variants behind the first-use consent prompt.
- **Hook trust boundary.** Shell hooks run with full user credentials (`security.md`); keep scripts in `~/.hermes/agent-hooks/`, review via `hermes hooks doctor`, use the consent allowlist.
- **Cron headless danger.** Governance jobs may hit dangerous-command prompts; `approvals.cron_mode: deny` keeps them fail-closed; jobs are read-mostly over the local DB.
- **Sensitivity under-flagging.** CIE flags personal at ~1/50th of Qwen; until reconciled, **do not** let the CIE sensitivity field gate promotion — gate on the stricter classifier (or require both).
- **Long-tail coverage gap.** 39 windows still overflow; the rerun must report residual overflow rather than claim full coverage.
- **Cache stability.** All injected guardrail context goes through `pre_llm_call`/user-message path (never the system prompt), preserving prompt caching per `prompt-assembly.md`.
- **No-signal implausibility.** `no_signal_windows: 0` indicates the triage/no-signal stage was unimplemented; governance must not infer "all windows had signal."
