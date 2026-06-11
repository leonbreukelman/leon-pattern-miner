#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${MINER_DB:-runtime/miner.db}"
LLM_URL="${LLM_URL:-http://127.0.0.1:8080}"
LLM_MAX_SESSIONS="${LLM_MAX_SESSIONS:-1}"
LLM_TIMEOUT="${LLM_TIMEOUT:-600}"
RUN_ID="${RUN_ID:-full-corpus-latest}"
REPORT="${REPORT:-reports/${RUN_ID}.md}"

mkdir -p runtime reports

echo "[$(date -Is)] monitor start db=$DB run_id=$RUN_ID llm_max_sessions=$LLM_MAX_SESSIONS llm_timeout=$LLM_TIMEOUT"
uv run miner --db "$DB" status --check-llm --llm-url "$LLM_URL" || true
uv run miner --db "$DB" retry-failed >/dev/null
uv run miner --db "$DB" extract --full-corpus --use-llm --llm-url "$LLM_URL" --llm-max-sessions "$LLM_MAX_SESSIONS" --llm-timeout "$LLM_TIMEOUT"
uv run miner --db "$DB" report --run-id "$RUN_ID" --output "$REPORT"
uv run miner --db "$DB" status --check-llm --llm-url "$LLM_URL" || true
echo "[$(date -Is)] monitor done report=$REPORT"
