#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${MINER_DB:-runtime/miner.db}"
LLM_BATCH="${LLM_BATCH:-3}"
LLM_TIMEOUT="${LLM_TIMEOUT:-240}"
SLEEP_SECONDS="${SLEEP_SECONDS:-10}"
LOG="${MONITOR_LOG:-runtime/full_corpus_monitor.log}"
mkdir -p runtime reports

remaining() {
  uv run python - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
print(conn.execute("""
select count(*) from sessions s
where s.status in ('normalized','extracted','verified')
  and not exists (
    select 1 from records r
    where r.session_id=s.session_id
      and r.extractor_version='local-qwen3-32b-q4km-v3'
  )
  and (
    select count(*) from errors e
    where e.session_id=s.session_id
      and e.error_class='llm_extract_error'
  ) < 2
""").fetchone()[0])
PY
}

{
  echo "[$(date -Is)] full corpus monitor loop start db=$DB batch=$LLM_BATCH timeout=$LLM_TIMEOUT"
  while true; do
    left="$(remaining)"
    echo "[$(date -Is)] llm_remaining_under_retry_cap=$left"
    if [[ "$left" == "0" ]]; then
      break
    fi
    LLM_MAX_SESSIONS="$LLM_BATCH" LLM_TIMEOUT="$LLM_TIMEOUT" RUN_ID="full-corpus-latest" REPORT="reports/full-corpus-latest.md" scripts/monitor_once.sh
    sleep "$SLEEP_SECONDS"
  done
  echo "[$(date -Is)] full corpus monitor loop complete"
} | tee -a "$LOG"
