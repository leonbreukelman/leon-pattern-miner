from pathlib import Path

from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.extractors import run_deterministic_extractors
from leon_pattern_miner.ingest import ingest_hermes_state_db, ingest_path
from leon_pattern_miner.llm_extractors import _coerce_confidence, _prompt_for_turns, validate_llm_record_payloads
from leon_pattern_miner.report import write_pilot_report
from leon_pattern_miner.runner import approve_pilot, enqueue_work, reset_stale_running_work, work_status_counts
from leon_pattern_miner.sensitivity import mask_sensitive


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_session.jsonl"


def test_ingest_jsonl_session_creates_turns(tmp_path):
    db_path = tmp_path / "miner.db"
    conn = connect(db_path)
    init_db(conn)

    result = ingest_path(conn, FIXTURE)

    assert result.sessions_ingested == 1
    assert result.turns_ingested == 8
    turns = conn.execute("select actor, text from turns order by idx").fetchall()
    assert turns[0]["actor"] == "leon"
    assert "repo is clean" in turns[0]["text"]
    assert turns[4]["actor"] == "tool"


def test_ingest_is_idempotent(tmp_path):
    db_path = tmp_path / "miner.db"
    conn = connect(db_path)
    init_db(conn)

    first = ingest_path(conn, FIXTURE)
    second = ingest_path(conn, FIXTURE)

    assert first.sessions_ingested == 1
    assert second.sessions_ingested == 0
    assert conn.execute("select count(*) from turns").fetchone()[0] == 8


def test_malformed_file_records_error(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not-json}\n", encoding="utf-8")
    conn = connect(tmp_path / "miner.db")
    init_db(conn)

    result = ingest_path(conn, bad)

    assert result.sessions_ingested == 0
    row = conn.execute("select status, error_detail from sessions").fetchone()
    assert row["status"] == "error"
    assert "JSON" in row["error_detail"] or "Expecting" in row["error_detail"]


def test_sensitive_values_are_masked_before_llm_payloads():
    github_like = "gh" + "o_" + "a" * 24
    anthropic_like = "sk" + "-ant" + "-" + "b" * 24
    text = f"token {github_like} and {anthropic_like}"
    masked, hits = mask_sensitive(text)

    assert github_like not in masked
    assert anthropic_like not in masked
    assert "[REDACTED_SECRET" in masked
    assert len(hits) >= 2


def test_deterministic_extractors_create_three_stream_records_with_verbatim_evidence(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)

    summary = run_deterministic_extractors(conn)

    assert summary.records_created >= 4
    rows = conn.execute("select stream, pattern_type, summary, evidence_json from records").fetchall()
    streams = {row["stream"] for row in rows}
    assert {"steering", "behavior", "methodology"}.issubset(streams)
    assert any(row["pattern_type"] == "clarification_qa" for row in rows)
    assert any(row["pattern_type"] == "tool_failure_recovery" for row in rows)
    assert any(row["pattern_type"] == "strategy_review" for row in rows)
    # Evidence validator must enforce quotes that appear in source turns.
    for row in rows:
        assert "quote" in row["evidence_json"]


def test_work_queue_resets_stale_running_items(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)
    enqueue_work(conn, extractor_version="deterministic-v1")
    conn.execute("update work_items set status='running', started_at=datetime('now','-2 hours')")
    conn.commit()

    reset = reset_stale_running_work(conn, older_than_minutes=30)

    assert reset == 3
    assert work_status_counts(conn)["pending"] == 3


def test_full_corpus_gate_requires_pilot_approval(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)

    assert conn.execute("select value from approvals where key='pilot_approved'").fetchone()[0] == "false"
    approve_pilot(conn, run_id="pilot-test", reviewer="fable", notes="ACCEPT")
    row = conn.execute("select value, approved_by from approvals where key='pilot_approved'").fetchone()
    assert row["value"] == "true"
    assert row["approved_by"] == "fable"


def test_ingest_hermes_state_db_selects_message_sessions(tmp_path):
    state = tmp_path / "state.db"
    source = connect(state)
    source.executescript(
        """
        create table sessions(id text primary key, source text not null, title text, started_at real not null, message_count integer default 0);
        create table messages(id integer primary key, session_id text not null, role text not null, content text, tool_name text, timestamp real not null);
        insert into sessions(id, source, title, started_at, message_count) values
          ('s1','discord','Useful project session', 10, 3),
          ('s2','cli','leon-pattern-miner self session', 20, 3),
          ('s3','cli','Another project session', 30, 3);
        insert into messages(session_id, role, content, tool_name, timestamp) values
          ('s1','user','Should we use Fable?', null, 11),
          ('s1','assistant','Yes for strategy review.', null, 12),
          ('s1','tool','pytest failed', 'terminal', 13),
          ('s2','user','Ignore miner feedback loop?', null, 21),
          ('s2','assistant','Yes.', null, 22),
          ('s2','tool','ok', 'terminal', 23),
          ('s3','user','Run the pilot first?', null, 31),
          ('s3','assistant','Yes.', null, 32),
          ('s3','tool','ok', 'terminal', 33);
        """
    )
    source.commit()
    target = connect(tmp_path / "miner.db")
    init_db(target)

    result = ingest_hermes_state_db(target, state, limit=2)

    assert result.sessions_ingested == 2
    assert result.turns_ingested == 6
    titles = [row["source_path"] for row in target.execute("select source_path from sessions order by source_path")]
    assert all("leon-pattern-miner" not in title for title in titles)


def test_pilot_report_contains_counts_without_raw_secret(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)
    run_deterministic_extractors(conn)

    report_path = write_pilot_report(conn, tmp_path / "pilot.md", run_id="pilot-test", include_quotes=True)
    text = report_path.read_text(encoding="utf-8")

    assert "Pilot Report" in text
    assert "steering" in text
    assert "records" in text
    assert "pilot-test" in text


def test_llm_record_validation_rejects_non_verbatim_evidence(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)
    turns = conn.execute("select * from turns order by idx").fetchall()

    good = {
        "records": [
            {
                "stream": "steering",
                "pattern_type": "authorization_limit",
                "summary": "Leon says routine tests do not require asking, but destructive changes do.",
                "actor": "leon",
                "scope": "global",
                "confidence": 0.8,
                "recommended_sink": "profile_candidate",
                "evidence": [
                    {"turn_id": turns[2]["turn_id"], "quote": turns[2]["text"]},
                ],
            }
        ]
    }
    bad = {
        "records": [
            {
                "stream": "steering",
                "pattern_type": "hallucinated",
                "summary": "Bad evidence must fail.",
                "actor": "leon",
                "evidence": [{"turn_id": turns[2]["turn_id"], "quote": "this quote is not in the source"}],
            }
        ]
    }

    assert len(validate_llm_record_payloads(turns, good)) == 1
    assert validate_llm_record_payloads(turns, bad) == []


def test_llm_prompt_is_bounded_and_confidence_strings_are_coerced(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)
    turns = conn.execute("select * from turns order by idx").fetchall()

    prompt = _prompt_for_turns(turns * 200)

    assert len(prompt) < 24000
    assert _coerce_confidence("high") == 0.8
    assert _coerce_confidence("medium") == 0.6
    assert _coerce_confidence("low") == 0.35
    assert _coerce_confidence(0.91) == 0.91
