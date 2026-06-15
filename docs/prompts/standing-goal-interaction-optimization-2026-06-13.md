STANDING GOAL — Interaction-Optimization + CIE Governance (lead-dev decision)
Date: 2026-06-13
Owner: Leon (non-dev). Lead dev/decision-maker: Claude (Opus), Hermes.
Use: paste the GOAL BLOCK below into Hermes with /goal. Optionally prefix a turn
budget if your build supports it; otherwise the phase gates control pacing.

================================================================================
LEAD-DEV DECISION (why this stack)
================================================================================
You asked for C+D+E (the durable fix) and said if A->B gets us there quicker, do
A and B too. As lead dev I'm committing to ALL FIVE, sequenced for fastest payoff
with maximum REUSE of Hermes-native primitives and minimum custom code:

  Stack choice: Hermes built-ins first — SOUL.md doctrine, Skills (procedural
  memory), Hooks + write_approval gates, cron/delegation orchestration,
  session_search, checkpoints/Tirith/approvals, and the already-written CIE
  spec/plan. New bespoke code is allowed ONLY inside leon-pattern-miner for the
  governance pipeline (C), and only where no built-in covers it. No Hermes core
  source edits on the default path. No new frameworks.

  Sequence: A and B first (they remove the most-repeated friction — manual model
  routing and the hand-run critic loop — in ~1 session and unblock everything
  else). D in parallel (small infra: preflight + event annotation). C next (the
  real long-term payoff: mined intelligence actually flows into memory/skills
  under a gate), built strictly on the existing spec/plan with Opus's correction
  list applied. E last as an evaluate-then-decide (pluggable Mem0/Zep memory) —
  adopt only if A/B/C prove built-in memory is the limiter.

  Why this is best long-term: it makes YOUR stable routing + independent-review
  policy automatic, turns mined patterns into durable behavior change instead of
  one-off report files, and keeps the whole thing on supported, upgrade-safe
  Hermes rails so it survives core updates and model switches.

Source-of-truth artifacts already in this project:
  reports/interaction-optimization-feedback-2026-06-13.md
  docs/specs/hermes-native-mining-takeaways-remediation-spec-2026-06-13.md
  docs/plans/hermes-native-mining-takeaways-remediation-plan-2026-06-13.md
  reports/opus-review-hermes-remediation-again-2026-06-13.md  (correction list)

================================================================================
GOAL BLOCK  (everything below the line is the /goal text)
================================================================================
Deliver the Hermes interaction-optimization + CIE governance program for Leon, lead-dev-owned, reusing Hermes-native primitives with minimal custom code. Work autonomously across turns; pause only at the defined gates or a true blocker. Leon is not a developer — make all in-scope engineering decisions yourself, report in plain owner language, and escalate only genuine blockers (billing/spend, destructive/irreversible actions, auth/2FA/CAPTCHA, missing credentials, or commits/pushes/deploys beyond this scope).

Working dir: $HOME/projects/leon-pattern-miner. Hermes user-space: ~/.hermes. Do NOT edit the Hermes core source tree ($HOME/hermes) on the default path. Load and follow these skills before acting: claude-code (Hermes config/commands), writing-plans, subagent-driven-development, test-driven-development, conversation-archive-mining, autonomous-coding-agents, systematic-debugging. Read the four source-of-truth artifacts in this project (interaction-optimization feedback report, the remediation spec, the remediation plan, and the latest Opus correction review) and treat the Opus correction list as binding.

Global rules: reuse built-ins (SOUL.md, skills, hooks, write_approval gates, cron, delegation, session_search, checkpoints/Tirith/approvals) before writing any code; new code only inside leon-pattern-miner and only where no built-in covers it; TDD for every code change (failing test first); artifact-first (save specs/plans/reports under docs/ and reports/, keep tests green); promote NOTHING into memory or skills without the write_approval gate plus Leon sign-off; preserve the provisional-until-verified boundary; never fabricate results; verify every claim with real tool output. At each phase gate run ONE independent review pass — try Fable first, and if Fable is unavailable preflight-detect it and fall back to Opus, labeling which model reviewed. Keep the build123d/Bambu and unrelated MCP tools out of scope.

PHASE A — Routing + auto-review doctrine (built-ins only, no custom code).
Encode Leon's stable policy so he stops hand-routing every turn: add a concise standing doctrine to ~/.hermes/SOUL.md and create a skill (e.g. leon-model-routing-and-review) capturing: default deliverable flow = produce artifact, run ONE independent review (Fable→Opus fallback with availability preflight), patch, then report, unless Leon says "no review"; routing policy = local Qwen for volume/extraction, Opus for review/strategy/adjudication, Fable for low-confidence decisions; always preflight model availability and name the fallback. Verify with hermes skills list and a SOUL read-back. GATE A: show Leon the SOUL diff + skill summary and the independent review verdict; on approval continue.

PHASE B — Reusable critic-loop skill with preflight.
Create one skill that runs build → model-availability preflight → independent review → patch → save standardized report, callable in one line (e.g. "run the critic loop on X"). Reuse Phase A routing. Include the Fable→Opus preflight/fallback and a standard report shape. Verify by dry-running it on an existing artifact in this project. GATE B: report the skill + a sample run to Leon.

PHASE D — Preflight/doctor + background-event annotation (small infra, prefer skill/hooks over code).
Provide a lightweight capability preflight (models, web_extract backend, agent-browser, llama/local-inference) and make background-process lifecycle events arrive interpreted (annotate exit codes against intent, e.g. "exit 143 = intentional kill, not a failure"). Implement via a skill + Hermes hooks where possible; only add miner-local code if unavoidable. Verify the preflight catches a known-missing capability (e.g. the search-only web_extract backend) without erroring the turn. GATE D: report to Leon.

PHASE C — CIE governance + promotion pipeline (the payoff; code allowed in miner, TDD, spec-driven).
Implement the remediation plan in $HOME/projects/leon-pattern-miner with the Opus correction list applied IN THIS ORDER:
  C0 Recall gate FIRST: build the stratified gold set and measure precision/recall/F1 and quote fidelity for the local Qwen extractor vs Opus on the same sessions. If local recall is below an acceptable bar, branch: flag that the existing CIE corpus may be missing signal and recommend re-extraction BEFORE investing further governance — do not silently govern a weak corpus. Report the number to Leon at GATE C0.
  C1 Persist rejected records FORWARD-ONLY (cie_rejections with cause enums quote_not_found|code_not_in_codebook|missing_field|invalid_json|other); do NOT promise reconciling the historical 1352 (treat it as an unrecoverable aggregate unless a re-extraction is funded).
  C2 Rejection-cause triage report + sensitivity reconciliation (quantify the Qwen vs CIE personal/secret divergence; recommend which classifier may gate promotion).
  C3 Clustering/dedup so raw code counts become distinct-insight counts; report the distinct-insight-to-record ratio on a high-count code first.
  C4 Frontier adjudication orchestrated via Hermes delegation or a cron LLM job — but route ALL secret/personal-sensitive records to a LOCAL model only (resolve the privacy contradiction; nothing sensitive leaves the box). For headless governance jobs set security.tirith_fail_open: false.
  C5 Rerun the 95 errored long windows smaller/with output caps; report residual overflow.
  C6 Promotion queue: only candidates passing quote-verification AND adjudication AND source-grade/corroboration thresholds enter cie_promotion_queue; promotion into Hermes memory/skills happens ONLY through memory.write_approval + skills.write_approval after Leon sign-off. Set memory.write_approval and skills.write_approval ON as the real promotion floor (note that approvals.mode manual, approvals.cron_mode deny, and security.tirith_enabled are already defaults — confirm, don't oversell).
Keep all tests green (existing 43 plus new governance tests). GATE C: after C0, and again before any promotion, report to Leon.

PHASE E — Pluggable memory evaluation (decide, don't assume).
Evaluate Hermes's pluggable memory options (built-in vs Mem0 vs Zep/Graphiti vs Letta) ONLY against the concrete need this program surfaces (time-aware, queryable cross-session decision history). Recommend adopt-or-stay-built-in with a one-page rationale; implement only if it clearly wins and Leon approves. GATE E: written recommendation to Leon.

Definition of done: A+B+D shipped and verified; C implemented through the promotion queue with the recall gate measured and the corrections applied, tests green, nothing promoted without Leon sign-off; E delivered as a decision. Final deliverable: a plain-English owner report listing what shipped, what each phase changed, the recall number and its implication, what is now automatic that used to be manual, and any open decisions — plus the saved artifact paths. Save a running status file under docs/status/ and update it at each gate so progress survives compaction or a model switch.
