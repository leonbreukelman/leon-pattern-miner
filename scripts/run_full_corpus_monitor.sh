#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${MINER_DB:-runtime/miner.db}"
LLM_EXTRACTOR_VERSION="${LLM_EXTRACTOR_VERSION:-local-qwen3.6-35b-a3b-ud-q4km-c8192-v1}"
LLM_BATCH="${LLM_BATCH:-25}"
LLM_TIMEOUT="${LLM_TIMEOUT:-240}"
SLEEP_SECONDS="${SLEEP_SECONDS:-10}"
LOG="${MONITOR_LOG:-runtime/full_corpus_monitor.log}"
mkdir -p runtime reports

remaining() {
  uv run python - "$DB" "$LLM_EXTRACTOR_VERSION" <<'PY'
import sys
from leon_pattern_miner.db import connect, init_db
conn = connect(sys.argv[1])
init_db(conn)
version = sys.argv[2]
print(conn.execute("""
select count(*) from sessions s
where s.status in ('normalized','extracted','verified')
  and not exists (
    select 1 from llm_session_runs l
    where l.session_id=s.session_id
      and l.extractor_version=?
      and l.status='processed'
  )
  and not exists (
    select 1 from records r
    where r.session_id=s.session_id
      and r.extractor='local_llm'
      and r.extractor_version=?
  )
  and (
    select count(*) from errors e
    where e.session_id=s.session_id
      and e.error_class='llm_extract_error'
      and e.extractor_version=?
  ) < 2
""", (version, version, version)).fetchone()[0])
PY
}

{
  echo "[$(date -Is)] full corpus monitor loop start db=$DB extractor_version=$LLM_EXTRACTOR_VERSION batch=$LLM_BATCH timeout=$LLM_TIMEOUT"
  while true; do
    left="$(remaining)"
    echo "[$(date -Is)] llm_remaining_under_retry_cap=$left"
    if [[ "$left" == "0" ]]; then
      break
    fi
    LLM_EXTRACTOR_VERSION="$LLM_EXTRACTOR_VERSION" LLM_MAX_SESSIONS="$LLM_BATCH" LLM_TIMEOUT="$LLM_TIMEOUT" RUN_ID="full-corpus-latest" REPORT="reports/full-corpus-latest.md" scripts/monitor_once.sh
    sleep "$SLEEP_SECONDS"
  done
  echo "[$(date -Is)] full corpus monitor loop complete"
} | tee -a "$LOG"
