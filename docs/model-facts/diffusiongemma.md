# DiffusionGemma local runtime facts

Last verified: 2026-06-14

## Verified local artifacts

- llama.cpp DiffusionGemma branch: `$HOME/opt/llama.cpp-diffusiongemma`
- Checked out commit: `9b4dae81f`
- Build method: CUDA devel Docker (`nvidia/cuda:12.6.3-devel-ubuntu24.04`) because host has NVIDIA driver but no CUDA toolkit / `nvcc`.
- Built CLI: `$HOME/opt/llama.cpp-diffusiongemma/build-docker/bin/llama-diffusion-cli`
- Runtime wrapper: `scripts/llama_diffusion_cli_docker.sh`
- Downloaded model: `$HOME/models/diffusiongemma-26B-A4B-it-GGUF/diffusiongemma-26B-A4B-it-Q4_K_M.gguf`
- SHA256: `d2ca2c032ebfb23cf2d1794a3465e615c7545634d46b3c30652a26d8b07c4ad3`

## Model identity

- Official model: `google/diffusiongemma-26B-A4B-it`
- Practical local GGUF repo: `unsloth/diffusiongemma-26B-A4B-it-GGUF`
- First 4090 target file: `diffusiongemma-26B-A4B-it-Q4_K_M.gguf`
- Architecture: Gemma 4 26B A4B MoE, discrete text diffusion
- HF access at verification time: public / not gated
- License surface: Apache 2.0 plus Gemma terms

## Available GGUF files observed

- `diffusiongemma-26B-A4B-it-BF16.gguf` (~47 GB, not for single 4090)
- `diffusiongemma-26B-A4B-it-Q8_0.gguf` (~25 GB, too tight for first 24 GB run)
- `diffusiongemma-26B-A4B-it-Q6_K.gguf` (~21 GB)
- `diffusiongemma-26B-A4B-it-Q5_K_M.gguf` (~18 GB)
- `diffusiongemma-26B-A4B-it-Q4_K_M.gguf` (~16 GB, first target)

## Runtime facts / caveats

- Standard `llama-cli` and `llama-server` cannot generate DiffusionGemma GGUF at the time of verification.
- Unsloth documents the required runtime as the DiffusionGemma llama.cpp PR branch `ggml-org/llama.cpp#24423` and the dedicated `llama-diffusion-cli` target.
- The current `leon-pattern-llama` Qwen container occupies most 4090 VRAM when running; stop it before DiffusionGemma smoke tests.
- MinerMark OpenAI `/v1` runner remains the default path for Qwen and future OpenAI-compatible servers. DiffusionGemma GGUF uses `--adapter diffusion-cli` unless/until a server path is verified.
- Verified free-text smoke: loads on CUDA0 and produces coherent output with `-n 32`, `--diffusion-steps 16`.
- Verified adapter JSON smoke: `make_diffusion_cli_adapter(...)` returns `{'json': {'records': []}, 'model_ids': ['diffusiongemma-q4-smoke']}` on a JSON-constrained prompt.
- Real CIE prompt caveat: on the frozen `hermes:20260503_233818_858ef1` window, all-GPU `-n 512/768` OOMs; `--cpu-moe` allows `-n 768` but the model spends the canvas in `<|channel>thought` and does not emit the final `<channel|>{"records": [...]}` answer. Treat full MinerMark scoring as deferred until the no-thought/final-channel issue is solved or a larger-memory runtime is available.
- Channel-aware extraction (anti-false-scorecard guard): DiffusionGemma emits reasoning after `<|channel>thought` and the real answer after `<channel|>`. The diffusion adapter (`extract_answer_channel`) keeps ONLY the answer channel before JSON coercion. If a thought channel opened but no answer channel was emitted (the observed out-of-budget CIE failure), the adapter raises rather than salvaging records-shaped JSON out of the reasoning prose. Verified on real captured outputs: `diffusiongemma_smoke_json_raw.out` -> `{"records": []}`; `diffusiongemma_cie_raw_n768_cpu_moe.out` and `diffusiongemma_cie_compact_raw.out` -> empty answer channel -> adapter raises.

## Build target

```bash
mkdir -p ~/opt && cd ~/opt
if [ ! -d llama.cpp ]; then git clone https://github.com/ggml-org/llama.cpp; fi
cd llama.cpp
git fetch origin pull/24423/head:diffusiongemma
git checkout diffusiongemma
git rev-parse --short HEAD
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build -j --config Release --target llama-diffusion-cli
```

Record the exact commit hash after build and update this file.
