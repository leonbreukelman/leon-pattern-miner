#!/usr/bin/env bash
set -euo pipefail
IMAGE="${DIFFUSION_LLAMA_DOCKER_IMAGE:-nvidia/cuda:12.6.3-devel-ubuntu24.04}"
SRC_DIR="${DIFFUSION_LLAMA_SRC:-${HOME}/opt/llama.cpp-diffusiongemma}"
MODELS_DIR="${DIFFUSION_MODELS_DIR:-${HOME}/models}"
mkdir -p "$MODELS_DIR"
exec docker run --rm --gpus all -i \
  -v "$SRC_DIR:/src:ro" \
  -v "$MODELS_DIR:$MODELS_DIR:ro" \
  -v "$MODELS_DIR:/models:ro" \
  -v /tmp:/tmp:ro \
  -e LD_LIBRARY_PATH=/src/build-docker/bin \
  --entrypoint /src/build-docker/bin/llama-diffusion-cli \
  "$IMAGE" "$@"
