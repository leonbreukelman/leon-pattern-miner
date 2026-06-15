# Roadmap: CIE Extraction Benchmark ("does the 4090 model do its job?")

> **For the implementing coding agent:** follow this with the `writing-plans`,
> `test-driven-development`, and `leon-critic-loop` skills. The dataset already
> exists and is frozen — DO NOT re-run Opus or re-extract. Build only the harness.

> **2026-06-15 public-repo update:** the checked-in `benchmark/cie-extraction-v0/`
> payload is a synthetic/public-safe fixture that preserves the v0 scoring shape. Raw
> conversation-derived benchmark sessions stay local/ignored because the GitHub repo is
> public. Use the public fixture for harness regression and a private/sanitized gold set
> for real model-quality claims.

**Goal (one sentence):** Given a CIE gold-set dataset (public synthetic fixture for
harness regression, private/sanitized conversation-derived data for real quality),
provide a one-command harness that runs any extraction model over the same source
shape and produces a detailed scorecard vs the reference answer key.

**Status of public inputs:** DONE. The public fixture is built and frozen. Real
model-quality inputs remain private/sanitized and local-only unless separately approved.

---

## 0. What already exists (do not rebuild)

Public fixture at `benchmark/cie-extraction-v0/`:
- `manifest.json` — metadata, sizing, codebook hash, window params, scoring method,
  and the known Qwen baseline result.
- `sessions/<id>.json` — 15 synthetic public fixture sessions (287 turns).
- `gold/<id>.json` — 51 synthetic reference findings, preserving the answer-key shape.
- `baseline_qwen/<id>.json` — synthetic baseline preserving the original v0 score shape.

Reusable code already written and tested:
- `src/leon_pattern_miner/cie_recall.py` — `score_recall()` (recall/precision/F1,
  code-level + optional quote-strict), `stratified_session_sample()`, `Record`.
  6 passing tests in `tests/test_cie_recall.py`.
- `src/leon_pattern_miner/cie.py` — `build_session_windows`, `render_cie_prompt`,
  `validate_cie_payload`, `run_cie_harness(chat_func=...)`. The harness already
  accepts a pluggable `chat_func`, which is the seam a new model plugs into.
- `scripts/run_cie_recall_gate.py` — reference example of driving an arbitrary model
  (Opus) through the harness and scoring it. The new benchmark runner is a
  generalization of this.

## How big does the dataset need to be? (direct answer)

Statistical reality first: recall is a proportion, so its uncertainty is set by the
number of **gold findings**, not sessions, and findings are **clustered within
sessions** (not independent) — so effective sample size is smaller than the raw
count. Use Wilson 95% confidence intervals, never bare point estimates.

- **Public fixture: 15 synthetic sessions / 287 turns / 51 reference findings.** This
  catches harness/scorer regressions only. For a private v0-scale gold set with 51 real
  findings, recall CI is roughly **±0.13**, so it is still directional rather than
  authoritative.
- **Minimum for a *reliable* headline: ~150 gold findings** (~45-60 conversations).
  That pulls the overall CI to roughly ±0.07 — enough to state a recall number with
  a straight face and detect ~0.10-0.15 model deltas.
- **Comfortable / per-bucket-credible: ~300-400 gold findings** (~80-120
  conversations, ~25-30 findings per bucket). Only at this level can per-bucket
  recall be reported with meaningful CIs, and ~0.07-0.10 deltas become detectable —
  AND only if scored **paired** (same sessions, McNemar) rather than as two
  independent proportions, since unpaired comparison inflates variance by ~√2.

Honest correction to an earlier draft of this plan: claims like "detect 0.05 deltas
at 250-400 findings" were optimistic by ~1.5-2x once clustering and two-model
comparison variance are accounted for. The numbers above are the corrected guidance.

**Practical recommendation:** ship the harness now on the 15-session v0 set (it works
today and is honest as a directional smoke test), and grow the gold set to ~45-60
sessions / ≥150 findings (Phase 4, one-time Opus spend) before treating any headline
recall as authoritative or comparing models on small deltas.

---

## Architecture (reuse, do not reinvent)

```
benchmark/cie-extraction-v0/   (public fixture inputs + reference + baseline shape)
        |
        v
scripts/run_benchmark.py  --model <candidate>  --adapter <how-to-call-it>
        |  for each frozen session: build_session_windows -> render_cie_prompt
        |  -> candidate model (4090 endpoint / claude / any chat_func) -> validate_cie_payload
        v
benchmark/results/<model>-<timestamp>/
        predictions/<id>.json     (candidate's extraction, frozen for audit)
        scorecard.json            (overall + per-bucket recall/precision/F1, vs gold AND vs qwen baseline)
        scorecard.md              (human-readable; deltas vs baseline; pass/fail vs threshold)
```

Key design rules:
- The candidate model is just a `chat_func` (same seam Opus used). A 4090 model =
  point it at `http://127.0.0.1:8080/v1` (llama-server). No model-specific code.
- Scoring reuses `score_recall` unchanged. Report BOTH code-level and quote-strict.
- Inputs are read from the frozen `benchmark/` files, NEVER the live DB, so the
  benchmark is reproducible at the dataset level.
- The 4090 model itself is NOT run-to-run deterministic (GPU/batch nondeterminism,
  even at temp 0). Run each candidate `--runs N` (default 3) and report mean ± sd;
  record decode params (temp, max_tokens) in the scorecard.

## Statistical + validity rules the harness MUST follow (from review)

These are non-negotiable so the scorecard never implies more than the data supports:

1. **Confidence intervals, not point estimates.** Print a Wilson 95% CI next to every
   recall/agreement number, overall and per-bucket. At N<~30 findings/bucket, grey
   out or suppress the per-bucket point estimate (CI too wide to report).
2. **Non-determinism is real on the 4090.** llama-server/GPU inference is NOT
   reproducible even at temperature 0 (batch/kernel nondeterminism), and run-to-run
   variance at N=51 can exceed the noise floor. The runner MUST support `--runs N`
   (default 3), report **mean ± sd** across runs, and include a determinism note.
   Do NOT assert "everything is deterministic."
3. **Circularity banner (not a footnote).** The frozen Qwen baseline was extracted
   with few-shots that were Opus-seeded, giving it a home-field advantage against an
   Opus gold key. Any cross-model comparison against that baseline is NOT
   apples-to-apples — the scorecard must say so at the top.
4. **"precision" is mislabeled — call it agreement-with-Opus.** Opus is a strong
   reference, not ground truth. Use the label "agreement_with_opus" everywhere.
5. **Greedy matching is order-dependent.** `score_recall` consumes gold greedily,
   which can slightly under/over-count vs optimal bipartite matching. Either warn
   in the scorecard or switch to optimal (scipy linear_sum_assignment) — and add a
   test showing the chosen method is stable.
6. **Paired scoring for model-vs-model.** When comparing two candidates, score them
   on the SAME sessions and report the paired difference (McNemar-style), not two
   independent proportions.

## Implementation gaps to close (from review)
- Make the `leon-ops-preflight` and `leon-critic-loop` skill dependencies OPTIONAL:
  inline a minimal preflight (curl the local endpoint) and skip the review step
  gracefully if the skill/CLI is absent.
- Specify the frozen record schema in Phase 1 (open one `gold/*.json` first): each
  record has `codebook_code`, `unit`, `statement`, `actor`, `source_reliability`,
  `info_credibility`, `confidence`, `sensitivity`, and `evidence` = list of
  `{turn_id, quote}`. The benchmark matches on `codebook_code` + (optional) quote.

---



### Phase 1 — Dataset loader + integrity test  (no model calls)
- `src/leon_pattern_miner/benchmark.py`: `load_dataset(path)` -> sessions, gold,
  baseline, manifest. `Record` reuse from `cie_recall`.
- TEST FIRST `tests/test_benchmark.py`:
  - loads v0, asserts 15 sessions / 51 gold / manifest totals match files on disk.
  - asserts every gold record's `evidence[].turn_id` exists in its session (the gold
    is internally consistent).
- Verify: `pytest tests/test_benchmark.py -q`.

### Phase 2 — Scoring adapter over frozen files  (no model calls)
- `score_predictions(pred_dir, gold_dir)` -> overall + per-bucket metrics by reusing
  `score_recall`. Convert frozen JSON records -> `Record(session, code, quote)`.
- TEST FIRST: feeding the frozen `baseline_qwen` as "predictions" reproduces the
  known baseline (recall 0.35, per-bucket 0.50/0.40/0.28) within float tolerance.
  This is the golden regression that proves the harness matches the C0 result.
- Verify: `pytest -q`.

### Phase 3 — Candidate runner (the one command)
- `scripts/run_benchmark.py --dataset benchmark/cie-extraction-v0 --model <name>
  --base-url http://127.0.0.1:8080/v1 [--adapter local|claude] [--threshold 0.X]`.
- For each frozen session: `build_session_windows` (use manifest window params) ->
  `render_cie_prompt(family="all")` -> candidate `chat_func` -> `validate_cie_payload`
  -> collect predictions -> write `predictions/`, `scorecard.json`, `scorecard.md`.
- Reuse the `opus_chat`-style wrapper pattern from `run_cie_recall_gate.py` for the
  claude adapter; add a `local_chat` adapter that calls the existing `chat_json`
  from `cie.py` (already the 4090 path).
- Capability preflight FIRST via `leon-ops-preflight` (qwen up? model reachable?).
- Verify: run against the **current** 4090 Qwen as a candidate; scorecard recall
  should land near 0.35 (sanity: a model benchmarked against gold that helped seed
  its own few-shots may differ slightly — document it).
- Scorecard must include: overall recall/precision/F1 (code-level + quote-strict),
  per-bucket table, delta vs `baseline_qwen`, and PASS/FAIL vs `--threshold`.

### Phase 4 — (Optional, recommended) grow the gold set to ~45-60 sessions
- Reuse `scripts/run_cie_recall_gate.py` logic + `scripts/freeze_benchmark_dataset.py`
  with `per_bucket=15-20` and the SAME seed-extension approach to add sessions.
- This is the only step that costs Opus money; gate it on Leon. Compute $/call first.
- Bump dataset to `cie-extraction-v1`; keep v0 frozen for back-compat.

### Phase 5 — Make it a skill + one-line UX
- Skill `cie-benchmark` (category mlops/inference): "score any 4090 extraction model
  against the Opus gold set in one command," with the run command, threshold guidance,
  and the sizing rule-of-thumb above.
- A `make bench MODEL=...` or single `run_benchmark.py` invocation is the whole UX.

---

## Acceptance criteria (definition of done)
1. `pytest -q` green, including the Phase-2 regression that reproduces the 0.35
   baseline from frozen data. NOTE: this proves the harness is **self-consistent**
   (a regression guard), NOT that any model is "correct" — the scorecard must say so.
2. `scripts/run_benchmark.py --model <current-qwen> --runs 3` runs end-to-end against
   the frozen dataset with zero DB dependency and writes a scorecard with mean ± sd.
3. Scorecard shows overall + per-bucket recall/agreement/F1, code-level AND
   quote-strict, each with a Wilson 95% CI, plus delta-vs-baseline and PASS/FAIL vs
   threshold — with the circularity banner and sample-size warning at the top.
4. Swapping `--model`/`--base-url` to a different/modified 4090 model needs NO code
   change and produces a comparable scorecard.
5. A short `benchmark/README.md` explains: what the dataset is, the (corrected)
   sizing guidance, how to run it, how to read the scorecard, and what it can/cannot
   prove.

## What to defer
- Larger gold set (Phase 4) — needs Opus spend; gate on Leon.
- Multi-judge gold (e.g. a second frontier model) — only if we want true ground
  truth instead of "agreement with Opus."
- Per-field scoring (sensitivity, source_reliability accuracy) beyond find/miss —
  add once the find/miss benchmark is trusted.

## Anti-overclaim notes for the implementer (carry into the scorecard)
- Opus is a strong REFERENCE, not ground truth; "precision" = agreement with Opus.
- Code-level matching makes recall/precision LENIENT UPPER BOUNDS; always also report
  quote-strict.
- At 15 sessions / 51 findings this is DIRECTIONAL; print the sample size on every
  scorecard and warn against trusting sub-0.10 deltas until the gold set is grown.
