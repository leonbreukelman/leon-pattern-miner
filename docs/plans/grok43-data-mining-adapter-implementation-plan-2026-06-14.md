# Grok 4.3 Data-Mining Adapter Implementation Plan

> **For Hermes:** Implement this plan directly using strict TDD. Do not run a production corpus batch in this pass.

> **2026-06-15 supersession warning:** This plan produced useful xAI/Grok provider mechanics, but it is not the canonical extraction-quality path. The legacy `miner extract --use-llm` path used here routes through `llm_extractors.py`, which selects/truncates candidate turns and lacks CIE codebook/few-shots. Use CIE benchmark wiring before making any Grok quality or production-readiness claim.

**Goal:** Add a safe xAI Grok 4.3 adapter for provider-smoke testing, prove one actual extraction smoke writes the expected DB/status shape, and stop before any quality/production claim. As of 2026-06-15, the next required quality step is CIE benchmark wiring, not a direct corpus run.

**Architecture:** Add a generalized OpenAI-compatible provider transport in `llm.py` with local-vs-xAI config. This plan wired `run_llm_extractors()` and CLI flags for provider-smoke compatibility, but that legacy extractor is not sufficient for model-quality evaluation. Keep xAI calls gated by explicit `--confirm-live` and an actual outbound request ceiling. Store Grok output under a distinct extractor version and prove the same expected `{"records": [...]}` format through one-session live smoke.

**Tech Stack:** Python stdlib only (`urllib`, `json`, `dataclasses`), SQLite, uv/pytest/ruff, xAI OpenAI-compatible `/v1/chat/completions` with model `grok-4.3` and `XAI_API_KEY`.

---

## Acceptance criteria

1. Local Qwen path remains default and green; `--llm-provider` defaults to local.
2. xAI provider path requires `--confirm-live`, `--max-model-calls`, and `XAI_API_KEY`; dry-run does not construct a live client, run provider health, or make any network call.
3. xAI requests use `Authorization: Bearer ...`, model `grok-4.3`, visible assistant content only, and no local-only `/no_think` or `chat_template_kwargs`.
4. Remote prompts are masked by `mask_sensitive()` immediately before network send.
5. Truncation, empty visible content, reasoning-only content, malformed JSON, non-2xx, and 429/5xx produce typed/redacted failures rather than stored/scored records.
6. `run_llm_extractors()` accepts an injected/provider chat function and no longer directly hard-codes `chat_json()` for every LLM run.
7. Health/preflight is provider-aware; xAI does not silently no-op behind unauthenticated `health(base_url)`.
8. One live Grok 4.3 smoke against one session writes/updates `llm_session_runs`, returns expected JSON summary shape, and either creates quote-verified records or correctly records zero records/errors without corrupting status.
9. API call ceilings are actual outbound HTTP request ceilings, not session-count estimates. A malformed-JSON retry must consume a second permitted call or fail closed before retrying.
10. Opus reviews the plan before implementation and the implementation/results after live smoke; valid findings are fixed and re-reviewed/accepted before final handoff.

## Non-goals

- No production/full-corpus Grok run.
- No automatic promotion of mined records into memory/skills/wiki.
- No SDK dependency unless stdlib proves insufficient.
- No commit/push unless explicitly requested later.
- No Grok CLI batch route; the CLI is currently unauthenticated and is not a clean JSON batch API.

## Current code facts

- `src/leon_pattern_miner/llm.py` has local `chat_json()` and `health()`; both assume unauthenticated local llama-style endpoint.
- `src/leon_pattern_miner/llm_extractors.py` calls `chat_json(...)` directly and inserts `extractor='local_llm'`.
- `src/leon_pattern_miner/cli.py` exposes `--llm-url`, version, max sessions, timeout; no provider/model/live gate.
- `src/leon_pattern_miner/runner.py` progress queries filter on `records.extractor='local_llm'`.
- `scripts/monitor_once.sh` and `scripts/run_full_corpus_monitor.sh` are local-Qwen oriented.
- `src/leon_pattern_miner/cie.py` already has a `chat_func` seam, but `scripts/run_cie_qwen_harness.py` does not expose provider selection.

## Decided design choices after Opus plan review

- Keep the DB `records.extractor` value as `local_llm` for this pass and namespace Grok via `extractor_version` (`session-llm-grok4.3-xai-smoke-...`). This avoids breaking `runner.py` progress, local monitor scripts, and existing monitor tests. If we later want a provider-specific extractor column, that is a separate migration/test slice.
- The xAI path is implemented through `miner extract` CLI flags, not the existing monitor scripts. The monitor scripts remain local-Qwen defaults and must keep working unchanged.
- Live smoke uses a copied DB under `runtime/grok43-smoke-*.db`, not `runtime/miner.db` directly.
- Before live smoke, verify no local full-corpus monitor is running. If it is running, stop and report instead of touching live state.
- `--max-model-calls` means maximum actual outbound provider calls. For one session with one allowed retry, use `--max-model-calls 2`; `--max-model-calls 1` must reject/stop before a retry.
- Readiness at the end means “mechanically ready to attempt a bounded production Grok run,” not “Grok quality is proven.” One session can prove format/safety, not recall/quality.

## Task 1 — Provider transport tests first

**Objective:** Define the xAI/local provider contract without making live calls.

**Files:**
- Create: `tests/test_provider_chat.py`.
- Modify later: `src/leon_pattern_miner/llm.py`.

**RED tests:**
1. xAI request building:
   - monkeypatch `urllib.request.urlopen` and `os.environ`.
   - call a new function such as `chat_json_provider(prompt, provider=ProviderConfig(...))`.
   - assert URL is exactly `https://api.x.ai/v1/chat/completions` for both `https://api.x.ai` and `https://api.x.ai/v1` config.
   - assert `Authorization` header exists but test never prints key value.
   - assert body model is `grok-4.3`.
   - assert body does not include `/no_think` or `chat_template_kwargs` for xAI.
2. Local compatibility:
   - local provider still sends no Authorization header.
   - local provider still sends `/no_think` and `chat_template_kwargs`.
3. Masking:
   - prompt with secret-shaped content is not present raw in outgoing body; masked placeholder is present.
4. Failure handling:
   - missing key env raises before network.
   - HTTPError redacts key-shaped values.
   - `finish_reason='length'` raises.
   - empty content and reasoning-only content raise.
   - malformed JSON raises after one retry.
   - API call budget counts each outbound request; malformed-response retry is blocked when the budget is exhausted.
   - valid `{"records": []}` returns `{"json": {"records": []}, "masked_hits": N, "model_ids": [...]}`.

**Run RED:**
`uv run pytest tests/test_provider_chat.py -q`

Expected before implementation: import/function failures.

## Task 2 — Implement generalized provider transport

**Objective:** Add production code to satisfy Task 1 with stdlib-only implementation.

**Files:**
- Modify: `src/leon_pattern_miner/llm.py`.

**Implementation notes:**
- Add `OpenAIProviderConfig` dataclass with:
  - `base_url`, `model`, `api_key_env`, `send_local_no_think`, `send_chat_template_kwargs`, `response_format_json`, `provider_name`, and `max_tokens`-friendly call parameters.
- Add a request-budget object/callback checked immediately before every `urlopen` call.
- Keep `chat_json()` as local compatibility wrapper around the generalized function.
- Add `_normalise_chat_url(base_url)`.
- Add `health(..., api_key_env=None)` or a separate provider health/preflight helper.
- Add redaction helper for exception strings; never include Authorization or key values.
- Reject visible-content failures and truncation before JSON coercion.
- Keep one compact retry on malformed JSON, but only when request budget permits it.

**Run GREEN:**
`uv run pytest tests/test_provider_chat.py -q`

Then:
`uv run pytest tests/test_adapters.py tests/test_cie_harness.py -q`

## Task 3 — Extractor injection and provider-aware progress tests

**Objective:** Make full-corpus mining use provider-selected chat without breaking local progress/resume.

**Files:**
- Create: `tests/test_llm_provider_extractors.py` or add focused tests to `tests/test_core_pipeline.py`.
- Modify later: `src/leon_pattern_miner/llm_extractors.py`, `src/leon_pattern_miner/cli.py`.

**RED tests:**
1. `run_llm_extractors(..., chat_func=fake, health_check=fake_ok, extractor_version='session-llm-grok43-test')` calls fake chat and inserts records with existing `extractor='local_llm'` and new extractor_version.
2. A zero-record fake response still writes `llm_session_runs` processed marker for the Grok extractor version.
3. Existing-record skip/progress counts remain keyed by extractor_version while using the existing `extractor='local_llm'` column.
4. Provider health/preflight can be bypassed or injected; xAI path must not call unauthenticated local `health()`.
5. Monitor scripts remain local-only; tests should keep asserting the default local provider and existing `r.extractor='local_llm'` remaining-count behavior.
6. Existing fake chat functions with signature `(prompt, *, base_url, timeout)` still work; model/max_tokens forwarding must be tolerant.

**Run RED:**
`uv run pytest tests/test_llm_provider_extractors.py tests/test_core_pipeline.py::test_monitor_scripts_are_resilient_and_amortized -q`

Expected before implementation: signature/import failures.

## Task 4 — Implement extractor/CLI support

**Objective:** Wire configurable provider behavior through the mining stack while leaving monitors local-compatible.

**Files:**
- Modify: `src/leon_pattern_miner/llm_extractors.py`.
- Modify: `src/leon_pattern_miner/cli.py`.
- Do not modify `scripts/monitor_once.sh` / `scripts/run_full_corpus_monitor.sh` for xAI in this slice unless tests require preserving local defaults.

**Implementation notes:**
- Add `chat_func` injection to `run_llm_extractors()`.
- Add `health_check` injection or `skip_health` for provider-managed preflight.
- Add `llm_model`, `llm_provider`, `llm_api_key_env` support at CLI level. Do not add `llm_extractor` in this slice; keep the existing extractor column stable.
- Add `--dry-run`, `--confirm-live`, `--max-model-calls`.
- Planned calls for session-level extractor = sessions to attempt after resume/retry-cap filters. Dry-run reports planned sessions and the requested call ceiling.
- For non-local providers, refuse unless `--confirm-live` and `--max-model-calls` are present and planned sessions <= ceiling. The provider budget still enforces actual outbound calls, including retry calls.
- Keep `extractor='local_llm'` and distinguish Grok via extractor_version.
- Preserve backwards-compatible fake-chat signatures in existing tests: if a fake only accepts `prompt, base_url, timeout`, the extractor should not require `model` or `max_tokens` on that fake.

**Run GREEN:**
`uv run pytest tests/test_llm_provider_extractors.py tests/test_core_pipeline.py -q`

## Task 5 — CLI dry-run and no-confirm safety tests

**Objective:** Prove paid/live gate behavior mechanically before any live xAI call.

**Files:**
- Modify tests: likely `tests/test_core_pipeline.py` or new CLI test file.
- Modify: `src/leon_pattern_miner/cli.py`.

**RED tests:**
1. `miner extract --use-llm --llm-provider xai --dry-run` prints planned session/call count and exits 0 without requiring `XAI_API_KEY` and without provider health/preflight/network.
2. `miner extract --use-llm --llm-provider xai` without `--confirm-live` exits non-zero before network/model construction.
3. `--max-model-calls 0` with one planned session exits non-zero.
4. local provider path remains backwards-compatible.
5. `--llm-provider` default is local and monitor scripts still call the local path unchanged.

**Run:**
`uv run pytest tests/test_core_pipeline.py -q`

## Task 6 — Full local verification before live API

**Objective:** Prove no regressions before spending API calls.

**Commands:**
```bash
uv run pytest -q
uv run ruff check src/leon_pattern_miner tests scripts
python3 -m compileall src scripts
```

Expected:
- pytest green.
- ruff green or fixable issues resolved.
- compileall green.

## Task 7 — Actual xAI one-call and one-session smoke

**Objective:** Prove Grok 4.3 performs extraction and returns the expected format.

**API CALL:** This task sends masked conversation-mining prompt material to xAI and uses API quota. Scope is one tiny JSON smoke plus one one-session extraction smoke, not production.

**Preflight:**
- Check `XAI_API_KEY` presence without printing it.
- Verify no local full-corpus monitor process is running.
- Copy `runtime/miner.db` to `runtime/grok43-smoke-2026-06-14.db` and run the smoke against the copy.
- Dry-run planned calls.
- Use unique extractor version `session-llm-grok4.3-xai-smoke-20260614`.

**Commands shape (provider-smoke only; do not use as a Grok/model-quality or production-extraction command before the CIE benchmark/gold-set quality gate):**
```bash
uv run miner --db runtime/grok43-smoke-2026-06-14.db extract --use-llm \
  --llm-provider xai \
  --llm-url https://api.x.ai \
  --llm-model grok-4.3 \
  --llm-api-key-env XAI_API_KEY \
  --llm-extractor-version session-llm-grok4.3-xai-smoke-20260614 \
  --llm-max-sessions 1 \
  --llm-timeout 240 \
  --dry-run

uv run miner --db runtime/grok43-smoke-2026-06-14.db extract --use-llm \
  --llm-provider xai \
  --llm-url https://api.x.ai \
  --llm-model grok-4.3 \
  --llm-api-key-env XAI_API_KEY \
  --llm-extractor-version session-llm-grok4.3-xai-smoke-20260614 \
  --llm-max-sessions 1 \
  --llm-timeout 240 \
  --confirm-live --max-model-calls 2
```

**Post-smoke checks:**
- Query `llm_session_runs` for the smoke extractor version.
- Query `records` and `errors` for the smoke extractor version.
- Write a result artifact under `reports/grok43-smoke-2026-06-14.md` with:
  - commands;
  - planned calls;
  - API response metadata excluding secrets;
  - DB rows/counts;
  - whether records were quote-verified or zero-record processed;
  - whether mechanically ready/not ready for another bounded provider-smoke attempt.

## Task 8 — Opus implementation/results review

**Objective:** Have Opus attack the implementation and live smoke evidence before declaring readiness.

**Review input:**
- Plan file.
- Relevant diff summary or patch.
- `reports/grok43-smoke-2026-06-14.md`.
- Test command outputs.
- Secret-redacted provider metadata.

**Review prompt asks:**
- Is the implementation faithful to the plan?
- Are API/spend/privacy gates adequate?
- Did the live run prove expected extraction format?
- Are there hidden production-run blockers?
- Required fixes before production.

**If Opus returns corrections:**
- Convert valid issues into tests first where code behavior is implicated.
- Patch code/docs.
- Re-run targeted and full tests.
- Re-run only the minimum live smoke if the finding affects provider behavior.
- Run focused Opus re-review until ACCEPT / no blockers.

## Final readiness report

2026-06-15 correction: "ready" here means provider mechanics only. Production extraction readiness now also requires a CIE benchmark/gold-set quality gate.

Report only after Task 8 is accepted:

- direct status: ready/not ready for provider-mechanics smoke/bounded legacy-path test;
- exact dry-run/live command for that provider-mechanics scope, with call ceiling;
- verification results;
- Opus review verdict;
- remaining risks/cost/privacy caveats;
- current git status.

Do not run production until explicitly asked and until Grok has passed the CIE benchmark/gold-set quality gate.
