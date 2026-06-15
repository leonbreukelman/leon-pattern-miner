import json

from leon_pattern_miner.cli import main as cli_main
from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.llm import LLMHealth
from leon_pattern_miner.llm_extractors import run_llm_extractors
from leon_pattern_miner.runner import status_snapshot


def _seed_session(conn, session_id="s", turns=None):
    turns = turns or []
    conn.execute(
        "insert into sessions(session_id, source_path, format, turn_count, content_hash, status) values (?, ?, 'test', ?, ?, 'normalized')",
        (session_id, f"fixture:{session_id}", len(turns), session_id),
    )
    for idx, turn in enumerate(turns):
        actor, text, tool_name = turn
        conn.execute(
            "insert into turns(turn_id, session_id, idx, actor, text, tool_name, char_offset_start, char_offset_end) values (?, ?, ?, ?, ?, ?, 0, ?)",
            (f"{session_id}:{idx}", session_id, idx, actor, text, tool_name, len(text)),
        )
    conn.execute("update sessions set status='extracted' where session_id=?", (session_id,))
    conn.commit()


def test_run_llm_extractors_accepts_injected_chat_and_version_namespaces_grok(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, "grok-s", [("agent", "Should I verify before production?", None)])
    turn = conn.execute("select * from turns where session_id='grok-s'").fetchone()
    calls = []

    def fake_chat(prompt, *, base_url, timeout):
        calls.append({"prompt": prompt, "base_url": base_url, "timeout": timeout})
        return {
            "json": {
                "records": [
                    {
                        "stream": "behavior",
                        "pattern_type": "clarification_trigger",
                        "summary": "agent asks before production",
                        "actor": "agent",
                        "scope": "session",
                        "confidence": 0.7,
                        "recommended_sink": "report_only",
                        "evidence": [{"turn_id": turn["turn_id"], "quote": turn["text"]}],
                    }
                ]
            }
        }

    summary = run_llm_extractors(
        conn,
        base_url="https://api.x.ai",
        extractor_version="session-llm-grok43-test",
        max_sessions=1,
        chat_func=fake_chat,
        health_check=lambda base_url: LLMHealth(True, "provider ok"),
    )

    assert summary.sessions_processed == 1
    assert summary.records_created == 1
    assert calls and calls[0]["base_url"] == "https://api.x.ai"
    row = conn.execute("select extractor, extractor_version from records").fetchone()
    assert row["extractor"] == "local_llm"
    assert row["extractor_version"] == "session-llm-grok43-test"


def test_zero_record_grok_version_writes_processed_marker_and_progress_counts(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, "zero", [("agent", "No durable pattern here.", None)])

    summary = run_llm_extractors(
        conn,
        extractor_version="session-llm-grok43-zero-test",
        max_sessions=1,
        chat_func=lambda prompt, *, base_url, timeout: {"json": {"records": []}},
        health_check=lambda base_url: LLMHealth(True, "provider ok"),
    )

    assert summary.sessions_processed == 1
    assert summary.records_created == 0
    marker = conn.execute(
        "select status, records_created from llm_session_runs where extractor_version='session-llm-grok43-zero-test'"
    ).fetchone()
    assert dict(marker) == {"status": "processed", "records_created": 0}
    progress = status_snapshot(conn)["llm_progress"]["session-llm-grok43-zero-test"]
    assert progress["processed_sessions"] == 1
    assert progress["zero_record_processed_sessions"] == 1
    assert progress["remaining_under_retry_cap"] == 0


def test_provider_health_check_is_injected_not_hardcoded_local_health(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, "s", [("agent", "Should I run this?", None)])
    health_calls = []

    summary = run_llm_extractors(
        conn,
        base_url="https://api.x.ai",
        extractor_version="session-llm-grok43-health-test",
        max_sessions=1,
        chat_func=lambda prompt, *, base_url, timeout: {"json": {"records": []}},
        health_check=lambda base_url: health_calls.append(base_url) or LLMHealth(True, "xai ok"),
    )

    assert summary.errors == 0
    assert health_calls == ["https://api.x.ai"]


def test_cli_xai_dry_run_requires_no_key_or_network_and_reports_plan(tmp_path, monkeypatch, capsys):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn, "s", [("agent", "Should I verify?", None)])
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    rc = cli_main(
        [
            "--db",
            str(db),
            "extract",
            "--use-llm",
            "--llm-provider",
            "xai",
            "--llm-max-sessions",
            "1",
            "--dry-run",
        ]
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["llm"]["planned_sessions"] == 1
    assert out["llm"]["provider"] == "xai"
    assert out["llm"]["model"] == "grok-4.3"
    assert out["llm"]["reasoning_effort"] == "low"


def test_cli_xai_reasoning_effort_override_is_reported_in_dry_run(tmp_path, monkeypatch, capsys):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn, "s", [("agent", "Should I verify?", None)])
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    rc = cli_main(
        [
            "--db",
            str(db),
            "extract",
            "--use-llm",
            "--llm-provider",
            "xai",
            "--llm-reasoning-effort",
            "high",
            "--llm-max-sessions",
            "1",
            "--dry-run",
        ]
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["llm"]["reasoning_effort"] == "high"


def test_cli_xai_requires_confirm_live_and_call_ceiling(tmp_path, capsys):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn, "s", [("agent", "Should I verify?", None)])

    rc = cli_main(
        [
            "--db",
            str(db),
            "extract",
            "--use-llm",
            "--llm-provider",
            "xai",
            "--llm-model",
            "grok-4.3",
            "--llm-max-sessions",
            "1",
        ]
    )

    assert rc == 2
    assert "requires --confirm-live" in capsys.readouterr().out

    rc = cli_main(
        [
            "--db",
            str(db),
            "extract",
            "--use-llm",
            "--llm-provider",
            "xai",
            "--llm-model",
            "grok-4.3",
            "--llm-max-sessions",
            "1",
            "--confirm-live",
        ]
    )

    assert rc == 2
    assert "requires --max-model-calls" in capsys.readouterr().out

    rc = cli_main(
        [
            "--db",
            str(db),
            "extract",
            "--use-llm",
            "--llm-provider",
            "xai",
            "--llm-model",
            "grok-4.3",
            "--llm-max-sessions",
            "1",
            "--confirm-live",
            "--max-model-calls",
            "0",
        ]
    )

    assert rc == 2
    assert "planned calls exceed" in capsys.readouterr().out


def test_cli_default_provider_remains_local(tmp_path, monkeypatch):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn, "s", [("agent", "Should I verify?", None)])

    import leon_pattern_miner.cli as cli_module

    seen = {}

    def fake_run_llm_extractors(conn, **kwargs):
        seen.update(kwargs)
        from leon_pattern_miner.llm_extractors import LLMExtractSummary

        return LLMExtractSummary()

    monkeypatch.setattr(cli_module, "run_llm_extractors", fake_run_llm_extractors)

    rc = cli_main(["--db", str(db), "extract", "--use-llm", "--llm-max-sessions", "1"])

    assert rc == 0
    assert seen["base_url"] == "http://127.0.0.1:8080"
    assert seen["extractor_version"]
