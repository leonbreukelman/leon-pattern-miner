# leon-pattern-miner

Private local conversation miner for extracting Leon/Hermes autonomy patterns from local agent transcripts.

The system is deliberately conservative:

- reads local conversation archives;
- stores extracted records in a local SQLite DB;
- writes pilot/full reports under ignored local `reports/`;
- never auto-promotes records into Hermes memory or skills;
- blocks full-corpus runs until the 20-session pilot is reviewed and approved.

Streams:

1. **Leon steering** — recurring questions, directions, corrections, authorization semantics, escalation/non-escalation rules.
2. **Agent behavior** — clarification triggers, failure/recovery arcs, wasted loops, verification habits.
3. **Methodology** — emerging project-building methods across Leon projects.

Local LLM target: OpenAI-compatible llama.cpp server on `127.0.0.1:8080`, preferably `Qwen/Qwen3-32B-GGUF:Q4_K_M` on the RTX 4090. Deterministic fallback runs are labeled and do not count as full-quality extraction.
