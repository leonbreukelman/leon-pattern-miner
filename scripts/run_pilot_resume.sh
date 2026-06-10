#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DB="runtime/miner.db"
LOG="runtime/pilot_resume.log"
{
  echo "[$(date -Is)] pilot resume start"
  for i in $(seq 1 8); do
    echo "[$(date -Is)] llm resume chunk $i"
    uv run miner --db "$DB" extract --use-llm --llm-max-sessions 3 || true
    uv run miner --db "$DB" status --check-llm || true
  done
  uv run miner --db "$DB" report --run-id pilot-001 --output reports/pilot-001.md --include-quotes
  echo "[$(date -Is)] pilot resume done"
} | tee -a "$LOG"
