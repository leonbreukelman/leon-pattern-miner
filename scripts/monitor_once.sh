#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run miner --db runtime/miner.db status --check-llm
uv run miner --db runtime/miner.db retry-failed >/dev/null
uv run miner --db runtime/miner.db extract
uv run miner --db runtime/miner.db status --check-llm
