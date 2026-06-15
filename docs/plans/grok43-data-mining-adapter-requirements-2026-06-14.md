# Grok 4.3 data-mining adapter requirements

Date: 2026-06-14

2026-06-15 context update: this document is valid for **provider mechanics** only. It is superseded for extraction/model-quality decisions by the CIE benchmark path described in `AGENTS.md`, `README.md`, `docs/status/current-state.md`, and `benchmark/README.md`. Do not use the legacy `miner extract --use-llm` path described here to judge Grok quality or production readiness.

## Owner answer

Adding Grok 4.3 is a small provider-adapter job, not a rewrite, but the production data-mining path is not as adapterized as the new MinerMark benchmark path yet.

The benchmark path already has an adapter registry in `src/leon_pattern_miner/adapters.py`. The full-corpus data-mining path still hard-codes the local OpenAI-compatible llama/Qwen transport in `src/leon_pattern_miner/llm_extractors.py` via `chat_json(...)`, and the CLI exposes only `--llm-url`.

The right implementation is to generalize the OpenAI-compatible transport once, then wire it into the CIE benchmark/harness first. The older session-level LLM extractor may receive the provider transport for provider-smoke mechanics, but it is not the quality-evaluation path.

## Current repo facts

- `src/leon_pattern_miner/llm.py:57` has `chat_json(prompt, base_url, timeout, max_tokens, model)`.
  - It posts to `{base_url}/v1/chat/completions`.
  - It assumes a local unauthenticated server.
  - It sends local llama/Qwen-specific fields: `/no_think` and `chat_template_kwargs: {enable_thinking: False}`.
  - It has no `Authorization: Bearer ...` support.
- `src/leon_pattern_miner/llm_extractors.py:196` has `run_llm_extractors(...)`.
  - It calls `chat_json(...)` directly at line 244.
  - It cannot currently accept a provider-specific `chat_func`.
  - It inserts records with `extractor='local_llm'`, which would be misleading for Grok unless made configurable.
- `src/leon_pattern_miner/cie.py:617` has `run_cie_harness(..., chat_func=chat_json)`.
  - This path already has the necessary seam at the harness level.
  - The script `scripts/run_cie_qwen_harness.py` does not expose that seam yet; it is still Qwen/local-server oriented.
- `scripts/run_benchmark.py` and `src/leon_pattern_miner/adapters.py` already prove the adapter-registry pattern for MinerMark, but only for `openai` and `diffusion-cli`.
- xAI Grok 4.3 docs show an OpenAI-compatible `/v1/chat/completions` endpoint and model id `grok-4.3`.
- Current shell has `XAI_API_KEY` present. The `grok` CLI exists, but `grok models` reports `You are not authenticated`, so the subscription CLI route is not currently usable without `grok login`.

## Required implementation

### 1. Add an authenticated OpenAI-compatible provider adapter

Do not create a one-off Grok-only transport if avoidable. Add a generalized OpenAI-compatible adapter that can cover xAI, OpenAI, OpenRouter, Together, Fireworks, and local llama servers.

Suggested shape:

- Add config fields:
  - `provider`: e.g. `local-openai`, `xai`.
  - `base_url`: default local `http://127.0.0.1:8080`; xAI `https://api.x.ai` or `https://api.x.ai/v1` normalized internally.
  - `model`: for xAI, `grok-4.3`.
  - `api_key_env`: for xAI, `XAI_API_KEY`.
  - `send_local_no_think`: true only for local Qwen/llama, false for xAI.
  - `send_chat_template_kwargs`: true only for local llama.cpp-compatible servers, false for xAI.
  - `response_format_json`: true if provider supports JSON mode; smoke-test before assuming.
- Add Authorization header when `api_key_env` is configured.
- Keep using stdlib `urllib` unless a dependency is justified; `pyproject.toml` currently has no runtime dependencies.
- Apply `mask_sensitive()` at the remote transport boundary before bytes leave the machine. Do not rely only on upstream prompt construction; `chat_json()` currently has this final safety gate, and an xAI adapter must preserve it.
- Parse only visible assistant content from `choices[0].message.content`.
- Reject, do not silently accept:
  - HTTP non-2xx;
  - missing/empty assistant content;
  - empty visible content when the response has only reasoning/non-visible fields;
  - malformed JSON after one compact retry;
  - `finish_reason == 'length'` / truncation;
  - 429/5xx without either a capped backoff policy or a typed fail-fast error;
  - provider response without a `records` list.
- Redact Authorization/API-key material from all errors and logs.
- Bind the model id inside the adapter factory/config. `run_cie_harness()` does not forward `model` per call; it only passes `base_url`, `timeout`, and `max_tokens` into `chat_func`, so `grok-4.3` must be closed over by the adapter rather than expected as a runtime argument.
- Normalize `https://api.x.ai` and `https://api.x.ai/v1` to exactly one `/v1/chat/completions` path.

### 2. Wire the provider into the full-corpus extractor

Change `run_llm_extractors(...)` to accept an injectable `chat_func` or provider config instead of calling `chat_json(...)` directly.

Minimum clean change:

- `run_llm_extractors(..., chat_func=chat_json, model=None, extractor_name='local_llm')`.
- Call `chat_func(prompt, base_url=..., timeout=..., max_tokens=..., model=model)`.
- Make the current health gate provider-aware. `run_llm_extractors()` currently calls unauthenticated `health(base_url)` before processing; for xAI this must either send the Authorization header or be replaced/skipped by an authenticated provider preflight. Otherwise Grok mining silently returns `errors=1` and does no work.
- Make inserted record `extractor` configurable instead of hard-coded `local_llm`.
- Update the exact resume/progress/monitor sites that currently assume `records.extractor='local_llm'`, or deliberately keep `extractor='local_llm'` and use extractor_version as the namespace. If making `extractor` provider-specific, update at least:
  - `src/leon_pattern_miner/llm_extractors.py` existing-record check around line 223.
  - `src/leon_pattern_miner/runner.py` `llm_progress_counts()` filters around lines 51, 83, and 109.
  - `scripts/run_full_corpus_monitor.sh` remaining-count query around line 32.
  - `tests/test_core_pipeline.py` assertion around line 901, which currently expects the literal monitor query `r.extractor='local_llm'`.
- Require a new extractor version, e.g. `cie-v1-grok4.3-xai-20260614` or `session-llm-grok4.3-xai-20260614`. Distinct extractor_version already prevents Qwen/Grok record collisions; the hard-coded extractor column is mainly a naming/progress/reporting problem.

### 3. Wire the CLI/operator surface

For `miner extract --use-llm`, add flags such as the following for **provider-smoke only**. Do not use this legacy command family for Grok/model-quality evaluation; use the CIE benchmark path first.

- `--llm-provider {local-openai,xai}`
- `--llm-base-url` or reuse `--llm-url`
- `--llm-model grok-4.3`
- `--llm-api-key-env XAI_API_KEY`
- `--llm-extractor-name xai_grok`
- `--dry-run`
- `--confirm-live`
- `--max-model-calls N`

The live gates matter because a full-corpus run sends private transcript-derived prompts off-machine and can burn paid API quota. Credentials existing in the environment are not authorization to spend.

### 4. Add preflight and smoke paths

Before a batch run:

1. Provider preflight:
   - Verify `XAI_API_KEY` is set without printing it.
   - Query `/v1/models` or run one minimal chat call only after live/API authorization.
   - Confirm requested model id is accepted or record the served model id.
2. One-call JSON smoke:
   - Tiny prompt: return `{"records": []}`.
   - Verify strict JSON parsing and `records` envelope.
3. One-session mining smoke (**provider-smoke only, not quality evidence**):
   - `--llm-max-sessions 1 --confirm-live --max-model-calls 1`.
   - Inspect DB rows: records, errors, `llm_session_runs`, progress counters.
4. Only then run a bounded provider-smoke batch. Do not run a production-quality/corpus batch until Grok has passed the CIE benchmark/gold-set quality gate.

### 5. Add tests first

Required hermetic tests, no live API:

- Adapter builds xAI request with correct URL, model, JSON body, and `Authorization` header.
- Missing `XAI_API_KEY` fails before network.
- Error messages redact key-shaped values.
- Remote adapter masks the outgoing prompt at the transport boundary; the test should prove raw secret-shaped text is absent from the request body.
- Local provider keeps `/no_think`; xAI provider does not send local-only `/no_think` or `chat_template_kwargs`.
- Non-2xx provider response becomes a typed/redacted adapter error.
- 429/5xx follows the chosen bounded backoff/fail-fast policy.
- `finish_reason='length'` raises; no truncated JSON is scored.
- Empty visible content raises, including reasoning-only/non-visible-content responses.
- Valid response with `{"records": []}` returns the normal `{"json": ...}` contract.
- `run_llm_extractors` uses injected `chat_func` and stores configurable extractor/extractor_version.
- CLI dry-run computes planned calls without constructing a live client.
- CLI without `--confirm-live` refuses xAI live runs.
- Existing local-Qwen behavior remains green, but tests that pin the old monitor query (`test_core_pipeline.py:901`) must be updated if the `extractor` column becomes provider-specific.

Run:

```bash
uv run pytest -q
uv run ruff check src/leon_pattern_miner tests scripts
```

`pyright` is not currently installed in this project.

## Subscription CLI vs xAI API

Leon prefers subscription-first. There are two possible Grok routes:

1. `grok` CLI route:
   - Pros: subscription-style route if authenticated.
   - Current blocker: `grok models` reports `You are not authenticated` in this shell.
   - Engineering concern: the CLI is an agent/TUI surface, not a stable batch JSON API. It may add agent behavior/session state unless carefully invoked with `--single/--prompt-file --verbatim --output-format json --disable-web-search --tools ''` and proven with smokes.
   - I would not use this for a full mining batch unless the CLI can be made deterministic and JSON-clean.

2. xAI API route:
   - Pros: clean OpenAI-compatible `/v1/chat/completions`; easiest and most reliable adapter.
   - Current prerequisite: `XAI_API_KEY` is present in the environment, but no live call has been made in this assessment.
   - Requires explicit API/spend/privacy confirmation before smoke or batch.

Recommendation: implement the OpenAI-compatible provider adapter and use xAI API for the mining run after a one-call smoke. Re-authenticate/use the Grok CLI only if Leon explicitly wants the subscription route despite the batch reliability risk.

## Rough implementation size

- Adapter/config + provider-aware health: ~140-220 lines.
- Full-corpus extractor injection + progress/extractor-name cleanup: ~80-140 lines.
- CLI flags/dry-run/live gate: ~80-140 lines.
- Tests: ~150-250 lines.
- Docs/status update: small.

This is likely a half-day implementation if the xAI API accepts JSON mode as documented/expected; longer if Grok 4.3 needs provider-specific JSON coercion or rate-limit handling.

## Go/no-go criteria before full Grok mining

2026-06-15 correction: add a prior mandatory gate — Grok must score acceptably on the frozen CIE benchmark or an equivalent gold-set recall gate before any full mining run. One-session or 50-session legacy-extractor runs can prove provider mechanics only.

Go only when all are true:

- Unit tests and full suite pass.
- One-call xAI JSON smoke succeeds and records served/requested model metadata.
- One-session live mining smoke writes a processed marker, not just records.
- Status/report counters show the Grok extractor version distinctly from local Qwen.
- A dry-run call budget is printed and `--max-model-calls` matches it.
- Leon explicitly approves sending masked conversation-mining prompts to xAI and burning API quota.

Do not claim Grok is a replacement for local llama until MinerMark or a comparable gold-set recall gate shows it beats or justifies the cost versus the local Qwen baseline.
