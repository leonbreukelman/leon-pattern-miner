# DiffusionGemma 4090 smoke status — 2026-06-14

## Owner outcome

DiffusionGemma is built, downloaded, and runnable on the local RTX 4090 through a model-swappable MinerMark adapter path.

It is not ready for a full MinerMark scorecard yet: the CLI runtime can generate and the adapter can parse JSON envelopes, but on a real CIE window the model burns the available output canvas in `<|channel>thought` and does not emit the final `{"records": [...]}` envelope. Publishing a scorecard now would be misleading.

## Implemented in repo

- `src/leon_pattern_miner/adapters.py`
  - Adapter registry: `openai` and `diffusion-cli`.
  - Diffusion CLI subprocess adapter using list-form `subprocess.run(shell=False)`.
  - Non-interactive prompt-file delivery (`-f`) for long prompts.
  - Requires `records` JSON envelope for benchmark safety.
- `scripts/run_benchmark.py`
  - New `--adapter {openai,diffusion-cli}` switch.
  - Diffusion flags: binary, model, prompt mode/flag, GPU layers, ctx, steps, extra args.
  - Adapter-aware preflight.
- `scripts/llama_diffusion_cli_docker.sh`
  - Runs the built CLI inside `nvidia/cuda:12.6.3-devel-ubuntu24.04` with GPU access.
  - Avoids installing CUDA toolkit on host.
- `src/leon_pattern_miner/llm.py`
  - Public `coerce_json_content()`.
  - More robust JSON salvage for thought/prose/final-channel outputs.
- `src/leon_pattern_miner/benchmark.py`
  - Frozen sessions whose turns omit `session_id` now run correctly.
  - Scorecards now expose transport error-window and valid-JSON window rates.
- Docs:
  - `docs/plans/diffusiongemma-4090-minermark-implementation-plan-2026-06-14.md`
  - `docs/model-facts/diffusiongemma.md`

## Verified runtime artifacts

- llama.cpp DiffusionGemma checkout: `$HOME/opt/llama.cpp-diffusiongemma`
- Commit: `9b4dae81f`
- Built binary: `$HOME/opt/llama.cpp-diffusiongemma/build-docker/bin/llama-diffusion-cli`
- Runtime wrapper: `$HOME/projects/leon-pattern-miner/scripts/llama_diffusion_cli_docker.sh`
- Model: `$HOME/models/diffusiongemma-26B-A4B-it-GGUF/diffusiongemma-26B-A4B-it-Q4_K_M.gguf`
- SHA256: `d2ca2c032ebfb23cf2d1794a3465e615c7545634d46b3c30652a26d8b07c4ad3`

## Commands/results

### Build

Host CUDA build failed because the host lacks the CUDA toolkit / `nvcc`:

```text
CUDA Toolkit not found
```

Docker CUDA devel build succeeded:

```text
[100%] Built target llama-diffusion-cli
-rwxr-xr-x 1 root root 139K ... build-docker/bin/llama-diffusion-cli
```

### Download

```text
-rw-rw-r-- 1 <user> <user> 16G ... diffusiongemma-26B-A4B-it-Q4_K_M.gguf
d2ca2c032ebfb23cf2d1794a3465e615c7545634d46b3c30652a26d8b07c4ad3  diffusiongemma-26B-A4B-it-Q4_K_M.gguf
```

### VRAM

Before stopping Qwen:

```text
NVIDIA GeForce RTX 4090, 24564 MiB, 22140 MiB
/app/llama-server, 20282 MiB
```

After stopping `leon-pattern-llama`:

```text
NVIDIA GeForce RTX 4090, 24564 MiB, 1747 MiB
```

### Free-text smoke

Command shape:

```bash
scripts/llama_diffusion_cli_docker.sh \
  -m $HOME/models/diffusiongemma-26B-A4B-it-GGUF/diffusiongemma-26B-A4B-it-Q4_K_M.gguf \
  -p 'Reply with exactly: OK' \
  -n 32 -c 1024 -ngl 99 --diffusion-steps 16 --seed 1 --temp 0
```

Observed output included:

```text
<|channel>thought
...
<channel|>OK
...
sched_reserve: layer 5 is assigned to device CUDA0 ...
```

### Adapter JSON smoke

Python adapter call returned:

```text
{'json': {'records': []}, 'model_ids': ['diffusiongemma-q4-smoke']}
```

### Real CIE window smoke

Frozen session/window: `synthetic:diffusiongemma_window_01`.

- All-GPU `-n 512` and `-n 768`: OOM.
- `--cpu-moe -n 768`: fits and runs on CUDA0, but output remains in thought channel and does not emit a valid `records` envelope.
- Compact CIE prompt: model identifies plausible records in thought text but still does not emit the final JSON envelope.

Representative performance with `--cpu-moe -n 768`:

```text
total time: 42771.12ms, time per step: 972.07ms (44 steps over 3 blocks, entropy-bound)
throughput: 18.0 tok/s
```

## Test status

```text
uv run pytest -q
66 passed in 2.11s
```

## Post-review hardening (Opus ACCEPT, 1 warning actioned)

Independent Opus implementation review returned `verdict: ACCEPT`, zero blocking findings, `ready_to_report_to_owner: true`. Its top non-blocking warning (latent false-record risk: lenient JSON salvage could accept records-shaped JSON from the thought channel) was fixed before reporting:

- `extract_answer_channel()` in `adapters.py` keeps only the `<channel|>` answer segment; thought-only output now raises instead of salvaging.
- 3 new tests: answer-vs-thought channel selection, thought-channel records ignored, thought-only output raises.
- Verified on real captured outputs (not just synthetic fixtures).
- Temp-file leak (assign `temp_path` before write) fixed.

Remaining Opus warnings are documented as open items, not blockers (see below).


## Current call for full MinerMark

Do not run or report a full DiffusionGemma MinerMark scorecard yet. The safe current status is:

- 4090 runtime: PASS.
- Model-swappable benchmark adapter: PASS.
- Simple JSON envelope through adapter: PASS.
- Full CIE JSON envelope on frozen window: FAIL / deferred.

Next likely fixes:

1. Find a DiffusionGemma runtime/chat-template flag that suppresses `<|channel>thought` or starts directly in the answer/final channel.
2. Try a larger quant/runtime only if more VRAM is available; the 24 GB 4090 is tight for multi-block CIE output unless MoE is CPU-offloaded.
3. Add a CIE-specific compact prompt mode if we want to evaluate DiffusionGemma as a reasoning extractor separately from the existing full MinerMark prompt.
