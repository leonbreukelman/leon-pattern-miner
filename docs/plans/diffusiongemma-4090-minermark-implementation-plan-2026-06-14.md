# DiffusionGemma model-swap smoke + MinerMark path — implementation plan

Date: 2026-06-14 · Owner: Leon · Status: proposed (not yet executed on hardware)

## 0. Scope and honesty rules

Goal: make it cheap and reversible to point the **existing** MinerMark harness at Google's DiffusionGemma on the local RTX 4090, get a qualitative smoke first, and only claim a public harness result once the path can emit valid CIE JSON over the checked-in synthetic `benchmark/cie-extraction-v0` fixture. Real model-quality claims require a private/sanitized CIE gold set.

Non-goals: rebuilding the dataset, re-extracting from `runtime/miner.db`, changing scoring, or hardcoding DiffusionGemma into the runner.

Two facts that shape everything:

1. **The public model/runtime facts are already verified in this session and should be saved in-repo before execution.** The target is `google/diffusiongemma-26B-A4B-it`; the practical first local artifact is `unsloth/diffusiongemma-26B-A4B-it-GGUF` with `diffusiongemma-26B-A4B-it-Q4_K_M.gguf` (~16 GB, fits a 24 GB 4090). Standard `llama-cli` / `llama-server` cannot generate it yet; Unsloth says it needs llama.cpp PR #24423 and `llama-diffusion-cli`.
2. **The runner is already model-agnostic.** `run_candidate(..., chat_func=...)` calls `chat_func(prompt, *, base_url, timeout, max_tokens, model)` and expects `{"json": <payload>}` (optionally `{"model_ids": [...]}`). `scripts/run_benchmark.py` hardwires `chat_func=chat_json` (the OpenAI client in `llm.py`). The swap is an **adapter** behind a registry, not runner surgery.

The crux risk is **transport**, not scoring: llama.cpp's diffusion support has historically shipped as a one-shot CLI (`llama-diffusion-cli` / the `diffusion` example), **not** an OpenAI `/v1` server. If that's still true, `chat_json` can't talk to it and we need a subprocess adapter. The plan supports both and picks at runtime from what Step 1 finds.

## 1. Recommended architecture

### 1.1 Adapter registry (the model-swap seam)

`--adapter` selects a `chat_func` factory; every adapter honors the **same contract** the runner already uses.

```
src/leon_pattern_miner/adapters.py   (new)

ChatFunc = Callable[..., dict]   # (prompt, *, base_url, timeout, max_tokens, model) -> {"json": ..., "model_ids"?: [...]}

ADAPTERS = {
    "openai":        make_openai_adapter,         # wraps existing llm.chat_json (Qwen today, any /v1 server)
    "diffusion-cli": make_diffusion_cli_adapter,  # subprocess wrapper around llama.cpp diffusion binary
}

def get_adapter(name: str, cfg: AdapterConfig) -> ChatFunc: ...
```

- `make_openai_adapter` returns `llm.chat_json` (or a thin partial) — the Qwen path today and any future OpenAI-compatible server (incl. a llama.cpp diffusion *server* if one lands). Zero behavior change → protects the green baseline.
- `make_diffusion_cli_adapter` returns a closure that shells out to the diffusion binary as a non-interactive one-shot command, captures stdout, and runs it through the **same** JSON salvage as the OpenAI path (1.3). It must use list-form `subprocess.run` with `shell=False`; pass long MinerMark prompts through stdin or a temporary prompt file, never one giant shell-quoted `-p` argument.

A name→factory map (not `if model.startswith("diffusion")`) satisfies "easy future model swap, not hardcode only DiffusionGemma": a third backend = one function, no call-site edits.

### 1.2 Wiring

- `scripts/run_benchmark.py`: add `--adapter {openai,diffusion-cli}` (default `openai`) plus diffusion passthrough flags (`--diffusion-bin`, `--diffusion-model`, `--diffusion-steps`, `--diffusion-extra-arg` repeatable). Resolve `chat_func = get_adapter(args.adapter, cfg)`. The `/v1/models` preflight is only meaningful for `openai`; for `diffusion-cli` use a "binary exists + 1 trivial generation succeeds" check or `--no-preflight`.
- `run_candidate` / `benchmark.py`: **unchanged** (treats `chat_func` as a black box).

### 1.3 JSON contract reuse

`llm._parse_json_content` already strips ``` ``` ``` fences, `<think>` blocks, and brace-slices the first `{...}`. Promote it to a shared `adapters._coerce_json(text) -> dict` (delegating to a public `llm.coerce_json_content`) used by **both** adapters — no duplicate regex. The adapter returns `{"json": coerced}`; if salvage fails it raises, and `_extract_session_predictions` already counts that as `errors` per window (`benchmark.py:269`) — a failed window degrades to "0 records," never a crash.

### 1.4 Optional runner hardening (only if needed)

`_extract_session_predictions` swallows all exceptions into `stats["errors"]` — good (no crash) but blind. Add opt-in `debug_errors: bool` recording `repr(exc)[:200]` into a capped `stats["error_samples"]`. Default off so baseline scorecard bytes are unchanged. TDD it (3.5). Skip if the smoke ladder is clean.

## 2. Exact file changes

| File | Change | Risk |
|---|---|---|
| `docs/model-facts/diffusiongemma.md` | **Create.** Verified facts: exact GGUF repo+quant, llama.cpp branch/commit, binary name, server-or-CLI, ctx len, diffusion steps, VRAM at load, can-it-emit-strict-JSON. Source of truth for adapter defaults. | low |
| `src/leon_pattern_miner/adapters.py` | **Create.** `AdapterConfig`, `ADAPTERS`, `get_adapter`, `make_openai_adapter` (returns `chat_json`), `make_diffusion_cli_adapter` (subprocess), shared `_coerce_json`. | med |
| `src/leon_pattern_miner/llm.py` | **Minimal.** Expose `_parse_json_content` as public `coerce_json_content` (keep alias). No behavior change. | low |
| `scripts/run_benchmark.py` | Add `--adapter` + diffusion flags; build `chat_func` via `get_adapter`; adapter-aware preflight. | med |
| `tests/test_adapters.py` | **Create.** Registry, contract conformance, subprocess arg-build, JSON salvage, error mapping — fake subprocess, no GPU/network. | low |
| `scripts/smoke_diffusion.py` | **Create (recommended).** One-window one-shot smoke printing raw output + coerced JSON. | low |
| `.gitignore` | Ensure `benchmark/results/`, `predictions/`, `runtime/`, `*.db`, `models/`, `*.gguf` ignored (§3 cloud-review). | low |
| `tests/test_benchmark.py` | Untouched. Its fake-chat test (`:53`) already pins the contract. | none |

No `benchmark.py` change unless §1.4.

## 3. TDD sequence (deterministic Python only — no GPU in CI)

Each step red→green before the next. Live model work (§5) only after these pass.

1. **Registry + unknown-adapter error.** `get_adapter("openai", cfg)` callable; `get_adapter("nope", cfg)` raises listing known names. → implement `ADAPTERS`/`get_adapter`.
2. **OpenAI adapter = existing path (no regression).** `make_openai_adapter(cfg)` forwards `base_url/timeout/max_tokens/model` unchanged. Guards Qwen baseline.
3. **Shared JSON salvage.** `_coerce_json` on: clean object; fenced ```json; `<think>…</think>` prefix; leading prose then `{...}`; trailing prose; garbage → raises. → delegate to `llm.coerce_json_content`.
4. **Diffusion-CLI adapter builds argv + maps output → contract.** Inject fake runner (`run=fake`, default `subprocess.run`): argv has binary/model/prompt delivery/step/ctx flags; stdout with `{"records":[…]}` → `{"json":..., "model_ids":[<model>]}`; non-zero exit / empty stdout → **raises** (asserts it does *not* return `{"records":[]}`); timeout forwarded; closure accepts exactly the runner kwargs `base_url`, `timeout`, `max_tokens`, `model` even if `base_url` is ignored.
5. **(If §1.4)** runner `debug_errors` capture; default off keeps scorecard bytes identical (extend existing fake-chat test).
6. **Full suite green:** `pytest -q` — existing baseline regression (`test_benchmark.py:34`, Qwen recall 0.353) must still pass, proving scoring untouched.

Run `pytest tests/test_adapters.py -q` per step, then `pytest -q` before declaring §3 done. Tests assert *plumbing*, never model quality.

## 4. Runtime setup — llama.cpp diffusion branch + GGUF

> All commands **proposed**; Step 1 may change binary/flag names. Pin a commit and record it. Build under `~/opt`, models under a git-ignored dir — **never** the repo tree.

### 4.1 Facts first (before any 10 GB download)
```bash
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv   # 4090 + free VRAM
curl -s http://127.0.0.1:8080/v1/models | head -c 300; echo         # is Qwen holding VRAM?
python3 - <<'PY'                                                     # inspect HF repo metadata without global hf CLI
import json, urllib.request
for mid in ['google/diffusiongemma-26B-A4B-it','unsloth/diffusiongemma-26B-A4B-it-GGUF']:
    print(mid)
    d=json.load(urllib.request.urlopen('https://huggingface.co/api/models/'+mid, timeout=20))
    print('gated=', d.get('gated'), 'siblings=', [s.get('rfilename') for s in d.get('siblings', []) if str(s.get('rfilename','')).endswith('.gguf')])
PY
# Confirm upstream: does llama.cpp expose a diffusion *server* or only a CLI example?
```

### 4.2 Build llama.cpp w/ CUDA (isolated)
```bash
mkdir -p ~/opt && cd ~/opt
if [ ! -d llama.cpp ]; then git clone https://github.com/ggml-org/llama.cpp; fi
cd llama.cpp
git fetch origin pull/24423/head:diffusiongemma
# If PR #24423 has merged, this branch may be unnecessary; otherwise this is required.
git checkout diffusiongemma
git rev-parse --short HEAD                             # record commit in model-facts
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89   # 89 = Ada / 4090
cmake --build build -j --config Release --target llama-diffusion-cli
ls build/bin | grep -iE 'diffusion'                   # CONFIRM the CLI exists
```

### 4.3 Download GGUF (git-ignored, disk-aware)
```bash
df -h ~                                                 # free space BEFORE pull
export MODELS_DIR=~/models; mkdir -p "$MODELS_DIR"      # NOT in repo
# hf download <org>/<DiffusionGemma-GGUF> <exact-file>.gguf --local-dir "$MODELS_DIR"
du -sh "$MODELS_DIR"/*                                  # record size in model-facts
```

### 4.4 Bring up — pick transport from Step 1
- **Diffusion-capable OpenAI server** (preferred — reuses `chat_json`):
  ```bash
  ~/opt/llama.cpp/build/bin/llama-server -m "$MODELS_DIR/<dg>.gguf" \
    --host 127.0.0.1 --port 8081 -ngl 999 -c <ctx> --jinja   # 8081 ≠ 8080, avoid Qwen collision
  curl -s http://127.0.0.1:8081/v1/models                     # adapter=openai, base-url .../8081/v1
  ```
- **CLI only** (likely): no server; `diffusion-cli` adapter shells out:
  ```bash
  ~/opt/llama.cpp/build/bin/llama-diffusion-cli -m "$MODELS_DIR/<dg>.gguf" \
    -p "<prompt>" --diffusion-steps <N> -ngl 999 -c <ctx>     # verify stdout + exit 0 first
  ```

## 5. Live 4090 smoke-test ladder (stop at first failing rung; report where)

- **Rung 0 — VRAM truth.** `nvidia-smi`; free VRAM *after* what's already running. If Qwen is up: coexist (only if both fit) or stop it first.
- **Rung 1 — Model loads.** Server (8081) or CLI on a 5-word prompt. Gate: stays up / exits 0, weights load, VRAM occupied. Proves build + GGUF + 4090.
- **Rung 2 — Free-text generation.** "Reply with OK." Gate: coherent non-empty output.
- **Rung 3 — JSON one CIE window.** `smoke_diffusion.py`: ONE session → ONE window via `render_cie_prompt(family="all")` → print **raw** + **coerced**. Gate: `_coerce_json` yields a dict with `records` (even `{"records":[]}`). Make-or-break for §6.
- **Rung 4 — Validation survives.** Feed Rung 3 payload through `validate_cie_payload`. Gate: ≥1 record validates, or principled empty.
- **Rung 5 — One full session, runs=1.** `run_benchmark.py --adapter <…> --runs 1` on a single short session. Gate: predictions + scorecard written, low `errors`, records survive, and scorecard visibly reports valid-JSON/error-window rate. Record per-window latency → extrapolate Rung 6 cost.
- **Rung 6 — Full set, runs≥3.** Only now the real MinerMark run over `cie-extraction-v0`. Report mean ± sd, Wilson CIs, agreement-with-Opus + circularity banners. Abort to §6 honest-deferral if the run has a high transport-error/invalid-JSON rate instead of publishing a misleading low-score card.

Rung 1–2 pass = "runs on the 4090" (smoke). Rung 6 pass = "has a MinerMark scorecard." Do not conflate.

## 6. Deferral — if the diffusion CLI can't reliably emit strict JSON

Expected failure mode (no `json_object` over CLI; `<think>`/prose contamination; weak long exact-quote JSON). Cheapest-first:

1. **Salvage harder.** `_coerce_json` brace-slice + one bounded retry with a terse "valid JSON only, `{"records":[]}` if none" reminder (mirrors `llm.py:54`).
2. **Constrain decoding.** If the binary accepts GBNF/JSON-schema, force the `records` envelope. Record support in model-facts.
3. **Reduce schema pressure.** Smaller windows / `family` instead of `"all"` → shorter JSON → higher valid rate.
4. **Stop at qualitative smoke (honest deferral).** If 1–3 fail, declare success only as smoke (Rungs 1–4) and **explicitly defer the scorecard.** Log valid-JSON % + sample failures + blocker in `docs/status/` and model-facts. **Do not** publish a scorecard built on mostly-empty windows — that reads as "DiffusionGemma scored low" when the truth is "transport couldn't emit JSON."
5. **Park, don't entangle.** All diffusion code stays behind `--adapter diffusion-cli`; the `openai`/Qwen default is untouched, so deferral costs nothing elsewhere.

## 7. Success criteria (owner language)

> **Smoke (minimum win):** "I built llama.cpp with CUDA, loaded DiffusionGemma on my 4090, and it generates coherent text and ≥1 valid CIE `records` JSON object from a real frozen window — with pasted output to prove it. Qwen `--adapter openai` and full `pytest` still pass."

> **Benchmark (full win):** "`run_benchmark.py --adapter <…> --runs 3` completed over the full `cie-extraction-v0` set, most windows produced valid JSON, and I have a `scorecard.md` with mean ± sd, Wilson CIs, and agreement-with-Opus + circularity banners — under git-ignored `benchmark/results/`, never the live DB."

> **Honest deferral (acceptable):** "DiffusionGemma runs on the 4090 but can't reliably emit strict CIE JSON over a CLI yet. I logged valid-JSON rate + sample failures in `docs/status/`, left the `openai` default untouched, and did not publish a misleading scorecard."

Every claim is gated on **pasted command output**, not assertion.

## 8. Adversarial risk register

| # | Fragile point | Why it bites | Mitigation |
|---|---|---|---|
| 1 | **CLI, not OpenAI server** | `chat_json` (`/v1/chat/completions`) can't reach a CLI | Step 1 verifies transport; `diffusion-cli` subprocess adapter; `openai` adapter ready if a server lands |
| 2 | **CLI interactivity / stdout shape** | REPL-ish, progress on stderr, partial frames; flag names drift by commit | Injectable runner; `smoke_diffusion.py` prints raw first; argv from `AdapterConfig`; pin commit, record flags |
| 3 | **Strict JSON** | Diffusion weak at long exact-quote schema JSON; no `json_object` over CLI | Shared `_coerce_json` + bounded retry + optional grammar + family-narrowing; §6 deferral |
| 4 | **Model-swap semantics** | Hardcoded branch rots | Name→factory `ADAPTERS`; runner unchanged; new backend = one function + tests |
| 5 | **GPU memory contention** | Qwen on :8080 may own most of 24 GB → OOM/swap | Rung 0 measures free VRAM; new server on **:8081**; explicit coexist-or-stop decision; 24 GB quant via HF skill |
| 6 | **Non-OpenAI endpoint vs runner** | Preflight + `model_ids` assume `/v1/models` | Adapter-aware preflight (binary-exists for CLI); adapter synthesizes `model_ids` from model path so provenance is still recorded |
| 7 | **Silent empty success** | Failing CLI coerced to `{"records":[]}` → false low score | Adapter **raises** on empty/non-zero exit (tested); runner counts window error; §6 forbids scorecards on mostly-empty windows |
| 8 | **Time / disk** | Multi-GB GGUF + CUDA build; diffusion decode × 15 × windows × 3 = hours | `df -h` before download; build/models git-ignored & out-of-repo; ladder starts at 1 window/1 session; Rung 5 latency extrapolates Rung 6 |
| 9 | **Cloud-review leakage** | Raw transcripts / `miner.db` / results enter a bundle | `.gitignore` `runtime/`, `*.db`, `benchmark/results/`, `models/`, `*.gguf`; only code+docs reviewable; frozen `benchmark/` source stays as committed |
| 10 | **Determinism overclaim** | GPU/diffusion non-deterministic at temp 0 | runs≥3, mean ± sd, existing determinism banner; tests assert plumbing only |

## 9. Open questions to resolve in Step 1 (block the rest)

1. Diffusion-capable OpenAI server, or CLI only? (→ adapter = `openai` vs `diffusion-cli`)
2. Exact GGUF repo + quant fitting 24 GB with working context for ~7.6k-token prompts?
3. Diffusion-step count / ctx flags and their latency cost? (→ Rung 6 estimate)
4. Can the binary take a grammar/JSON-schema constraint? (→ §6 rung 2 viability)

---

All implementation steps are authorized by the user request; proceed without another approval unless a true blocker appears (spend beyond normal subscription/tooling, credentials, destructive system change, or auth/CAPTCHA).