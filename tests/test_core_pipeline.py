import json
from pathlib import Path

from leon_pattern_miner.cli import build_parser
from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.extractors import run_deterministic_extractors
from leon_pattern_miner.ingest import ingest_hermes_state_db, ingest_path
from leon_pattern_miner.llm import LLMHealth, chat_json
from leon_pattern_miner.llm_extractors import _coerce_confidence, _prompt_for_turns, run_llm_extractors, validate_llm_record_payloads
from leon_pattern_miner.report import write_pilot_report
from leon_pattern_miner.runner import approve_pilot, enqueue_work, reset_stale_running_work, status_snapshot, work_status_counts
from leon_pattern_miner.sensitivity import mask_sensitive, sensitivity_for_text


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_session.jsonl"


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
    conn.commit()


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


def test_extract_cli_accepts_llm_timeout_for_slow_partial_offload():
    args = build_parser().parse_args(["extract", "--use-llm", "--llm-max-sessions", "1", "--llm-timeout", "600"])

    assert args.llm_timeout == 600


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
    assert any(row["pattern_type"] == "failure_recovery_arc" for row in rows)
    assert any(row["pattern_type"] == "plan_spike_build_verify" for row in rows)
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


def test_deterministic_work_items_are_marked_completed_after_extraction(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    ingest_path(conn, FIXTURE)
    enqueue_work(conn, extractor_version="deterministic-v1")

    assert work_status_counts(conn)["pending"] == 3

    run_deterministic_extractors(conn)

    counts = work_status_counts(conn)
    assert counts.get("pending", 0) == 0
    assert counts["completed"] == 3


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
          ('s3','cli','Another project session', 30, 3),
          ('s4','discord','Unrelated thread title', 40, 2);
        insert into messages(session_id, role, content, tool_name, timestamp) values
          ('s1','user','Should we use Fable?', null, 11),
          ('s1','assistant','Yes for strategy review.', null, 12),
          ('s1','tool','pytest failed', 'terminal', 13),
          ('s2','user','Ignore miner feedback loop?', null, 21),
          ('s2','assistant','Yes.', null, 22),
          ('s2','tool','ok', 'terminal', 23),
          ('s3','user','Run the pilot first?', null, 31),
          ('s3','assistant','Yes.', null, 32),
          ('s3','tool','ok', 'terminal', 33),
          ('s4','user','pilot-009 report for local conversation mining', null, 41),
          ('s4','assistant','pattern miner status', null, 42);
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


def test_ingest_hermes_state_db_limit_zero_means_all_sessions(tmp_path):
    state = tmp_path / "state.db"
    source = connect(state)
    source.executescript(
        """
        create table sessions(id text primary key, source text not null, title text, started_at real not null, message_count integer default 0);
        create table messages(id integer primary key, session_id text not null, role text not null, content text, tool_name text, timestamp real not null);
        insert into sessions(id, source, title, started_at, message_count) values
          ('s1','cli','One', 10, 1),
          ('s2','cli','Two', 20, 1),
          ('s3','cli','Three', 30, 1);
        insert into messages(session_id, role, content, tool_name, timestamp) values
          ('s1','user','Can we verify one?', null, 11),
          ('s2','user','Can we verify two?', null, 21),
          ('s3','user','Can we verify three?', null, 31);
        """
    )
    source.commit()
    target = connect(tmp_path / "miner.db")
    init_db(target)

    result = ingest_hermes_state_db(target, state, limit=0)

    assert result.sessions_ingested == 3
    assert target.execute("select count(*) from sessions").fetchone()[0] == 3


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


def test_chat_json_uses_bounded_generation_budget(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"records": []}'}}]}).encode()

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    import leon_pattern_miner.llm as llm_module

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)

    assert chat_json("extract this", timeout=321)["json"] == {"records": []}
    assert 1024 <= captured["payload"]["max_tokens"] <= 1536
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["payload"]["messages"][1]["content"].startswith("/no_think\n")
    assert captured["timeout"] == 321


def test_chat_json_retries_once_after_malformed_json(monkeypatch):
    payloads = [
        {"choices": [{"message": {"content": '{"records": ['}}]},
        {"choices": [{"message": {"content": '{"records": []}'}}]},
    ]
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(req, timeout):
        calls.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(payloads[len(calls) - 1])

    import leon_pattern_miner.llm as llm_module

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)

    assert chat_json("extract this") == {"json": {"records": []}, "masked_hits": 0}
    assert len(calls) == 2


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


def test_clarification_trigger_rejects_url_question_marks_and_agent_answers(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("agent", "I checked https://example.test/path?assessment=1 and it is reachable.", None),
            ("agent", "Yes. Does Hermes already do this? is a section heading inside my answer.", None),
            ("agent", "Should I proceed with the full corpus now?", None),
        ],
    )

    run_deterministic_extractors(conn)

    quotes = [row["evidence_json"] for row in conn.execute("select evidence_json from records where pattern_type='clarification_trigger'")]
    assert len(quotes) == 1
    assert "full corpus now?" in quotes[0]


def test_failure_recovery_arc_requires_structured_failure_not_error_word_prose(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("tool", "VERDICT: ACCEPT. Mentions FabricatedClaimError only as a class name in prose.", "terminal"),
            ("tool", "test_a ok\ntest_b ok", "terminal"),
            ("tool", "--- target candidates ---\n1 src/fmc_mcp/client.py 104.0\nexit_code: 1", "terminal"),
            ("tool", "Traceback (most recent call last):\nModuleNotFoundError: No module named x", "terminal"),
            ("tool", "command failed\nexit_code: 2", "terminal"),
        ],
    )

    run_deterministic_extractors(conn)

    rows = conn.execute("select evidence_json from records where pattern_type='failure_recovery_arc'").fetchall()
    assert len(rows) == 2
    joined = "\n".join(row["evidence_json"] for row in rows)
    assert "FabricatedClaimError only as a class name" not in joined
    assert "test_a ok" not in joined
    assert "target candidates" not in joined
    assert "ModuleNotFoundError" in joined
    assert "exit_code: 2" in joined


def test_llm_validation_enforces_role_and_pattern_specific_evidence(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("leon", "Will the network get DHCP from the customer site?", None),
            ("agent", "I opened https://example.test/a?x=1 and inspected it.", None),
            ("agent", "Should I run the pilot first?", None),
            ("tool", "VERDICT: ACCEPT mentions FabricatedClaimError in prose", "terminal"),
            ("tool", "ERROR: command failed\nexit_code: 1", "terminal"),
        ],
    )
    turns = conn.execute("select * from turns order by idx").fetchall()
    payload = {
        "records": [
            {"stream": "behavior", "pattern_type": "clarification_trigger", "summary": "wrong role", "actor": "agent", "evidence": [{"turn_id": turns[0]["turn_id"], "quote": turns[0]["text"]}]},
            {"stream": "behavior", "pattern_type": "clarification_trigger", "summary": "url only", "actor": "agent", "evidence": [{"turn_id": turns[1]["turn_id"], "quote": turns[1]["text"]}]},
            {"stream": "behavior", "pattern_type": "clarification_trigger", "summary": "real agent question", "actor": "agent", "evidence": [{"turn_id": turns[2]["turn_id"], "quote": turns[2]["text"]}]},
            {"stream": "behavior", "pattern_type": "failure_recovery_arc", "summary": "prose only", "actor": "agent", "evidence": [{"turn_id": turns[3]["turn_id"], "quote": turns[3]["text"]}]},
            {"stream": "behavior", "pattern_type": "failure_recovery_arc", "summary": "real failure", "actor": "agent", "evidence": [{"turn_id": turns[4]["turn_id"], "quote": turns[4]["text"]}]},
        ]
    }

    valid = validate_llm_record_payloads(turns, payload)

    assert [row["summary"] for row in valid] == ["real agent question", "real failure"]


def test_llm_validation_demotes_methodology_status_quotes_even_with_generic_summary(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            (
                "agent",
                "I’ll continue from the preserved task list: finish the synthetic persona, run an actual local browser/API dogfood, then only implement/refine gaps that the dogfood or Fable review exposes.",
                None,
            ),
        ],
    )
    turns = conn.execute("select * from turns order by idx").fetchall()
    payload = {
        "records": [
            {
                "stream": "methodology",
                "pattern_type": "strategy_review",
                "summary": "Reusable Fable/dogfood methodology",
                "actor": "agent",
                "scope": "global",
                "confidence": 0.8,
                "recommended_sink": "skill_candidate",
                "evidence": [{"turn_id": turns[0]["turn_id"], "quote": turns[0]["text"]}],
            }
        ]
    }

    valid = validate_llm_record_payloads(turns, payload)

    assert len(valid) == 1
    assert valid[0]["recommended_sink"] == "report_only"


def test_llm_extractor_skips_sessions_after_repeated_errors(monkeypatch, tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, session_id="a", turns=[("agent", "Should I run the stale failed session?", None)])
    _seed_session(conn, session_id="b", turns=[("agent", "Should I run the healthy session?", None)])
    conn.executemany(
        "insert into errors(session_id, error_class, payload_excerpt) values (?, 'llm_extract_error', 'prior failure')",
        [("a",), ("a",), ("a",)],
    )
    conn.commit()
    healthy_turn = conn.execute("select * from turns where session_id='b'").fetchone()

    def fake_chat_json(prompt, *, base_url, timeout):
        return {
            "json": {
                "records": [
                    {
                        "stream": "behavior",
                        "pattern_type": "clarification_trigger",
                        "summary": "healthy agent question",
                        "actor": "agent",
                        "scope": "session",
                        "confidence": 0.7,
                        "recommended_sink": "report_only",
                        "evidence": [{"turn_id": healthy_turn["turn_id"], "quote": healthy_turn["text"]}],
                    }
                ]
            },
            "masked_hits": 0,
        }

    import leon_pattern_miner.llm_extractors as llm_extractors_module

    monkeypatch.setattr(llm_extractors_module, "health", lambda base_url: LLMHealth(True, "ok"))
    monkeypatch.setattr(llm_extractors_module, "chat_json", fake_chat_json)

    summary = run_llm_extractors(conn, max_sessions=1)

    assert summary.sessions_processed == 1
    assert summary.errors == 0
    assert conn.execute("select count(*) from records where session_id='b'").fetchone()[0] == 1
    assert conn.execute("select count(*) from records where session_id='a'").fetchone()[0] == 0


def test_llm_extractor_marks_zero_record_sessions_processed_so_monitor_advances(monkeypatch, tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, session_id="a", turns=[("agent", "I have no durable pattern here.", None)])
    _seed_session(conn, session_id="b", turns=[("agent", "Should I ask before deployment?", None)])
    b_turn = conn.execute("select * from turns where session_id='b'").fetchone()
    calls = []

    def fake_chat_json(prompt, *, base_url, timeout):
        if "turn_id=a:0" in prompt:
            calls.append("a")
            return {"json": {"records": []}, "masked_hits": 0}
        calls.append("b")
        return {
            "json": {
                "records": [
                    {
                        "stream": "behavior",
                        "pattern_type": "clarification_trigger",
                        "summary": "agent asks before deploy",
                        "actor": "agent",
                        "scope": "session",
                        "confidence": 0.7,
                        "recommended_sink": "report_only",
                        "evidence": [{"turn_id": b_turn["turn_id"], "quote": b_turn["text"]}],
                    }
                ]
            },
            "masked_hits": 0,
        }

    import leon_pattern_miner.llm_extractors as llm_extractors_module

    monkeypatch.setattr(llm_extractors_module, "health", lambda base_url: LLMHealth(True, "ok"))
    monkeypatch.setattr(llm_extractors_module, "chat_json", fake_chat_json)

    first = run_llm_extractors(conn, max_sessions=1)
    second = run_llm_extractors(conn, max_sessions=1)

    assert first.sessions_processed == 1
    assert first.records_created == 0
    assert second.sessions_processed == 1
    assert second.records_created == 1
    assert calls == ["a", "b"]


def test_career_and_employer_content_is_personal():
    assert sensitivity_for_text("Smurfit Westrock employment history and resume positioning") == "personal"


def test_report_samples_top_patterns_not_only_first_rows(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("leon", "Can we verify this?", None),
            ("agent", "I will plan, test, implement, and verify the change.", None),
            ("tool", "exit_code: 1\nERROR: command failed for real", "terminal"),
            ("agent", "Should I ask before deploy?", None),
        ],
    )
    run_deterministic_extractors(conn)

    report = write_pilot_report(conn, tmp_path / "pilot.md", run_id="sample-test", include_quotes=True, max_examples=1)
    text = report.read_text(encoding="utf-8")

    assert "## Examples by top pattern" in text
    assert "plan_spike_build_verify" in text
    assert "failure_recovery_arc" in text
    assert "ERROR: command failed for real" in text


def test_harness_important_messages_are_filtered_from_ingest(tmp_path):
    fixture = tmp_path / "harness.jsonl"
    fixture.write_text(
        '\n'.join(
            [
                '{"role":"user","content":"[IMPORTANT: The user has invoked the \\\"disciplined-project-delivery\\\" skill. Do not mine this.]"}',
                '{"role":"user","content":"[IMPORTANT: Background process proc_123 completed (exit code -15).]"}',
                '{"role":"user","content":"What was the last work done?"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )
    conn = connect(tmp_path / "miner.db")
    init_db(conn)

    result = ingest_path(conn, fixture)

    assert result.turns_ingested == 1
    text = conn.execute("select text from turns").fetchone()["text"]
    assert text == "What was the last work done?"


def test_duplicate_quote_content_is_deduped_across_patterns(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    quote = "Can we plan and verify this?"
    _seed_session(conn, turns=[("leon", quote, None), ("leon", quote, None)])

    run_deterministic_extractors(conn)

    evidence_quotes = [row["evidence_json"] for row in conn.execute("select evidence_json from records")]
    assert sum(quote in evidence for evidence in evidence_quotes) == 1


def test_canonical_pattern_names_only(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("leon", "Can we verify this?", None),
            ("leon", "No, do not deploy without approval.", None),
            ("leon", "Actually, use Fable for strategy only.", None),
            ("agent", "I will plan, test, implement, and verify the change.", None),
            ("tool", "ERROR: command failed\nexit_code: 1", "terminal"),
        ],
    )

    run_deterministic_extractors(conn)

    patterns = {row["pattern_type"] for row in conn.execute("select pattern_type from records")}
    assert "correction_or_authorization_boundary" not in patterns
    assert "plan_test_verify_loop" not in patterns
    assert "tool_failure_recovery" not in patterns
    assert {"clarification_qa", "correction", "plan_spike_build_verify", "failure_recovery_arc"}.issubset(patterns)


def test_steering_and_methodology_reject_narration_and_single_keyword_noise(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("leon", "I'll answer from the actual Hermes/CLI capabilities rather than guessing.", None),
            ("leon", "I checked https://example.test/path?assessment=1 already.", None),
            ("leon", "There are tickets open on kanban.", None),
            ("agent", "Prompt saved. I'll run Fable with that prompt and capture the review.", None),
            ("leon", "Can we plan and verify this before full corpus?", None),
            ("agent", "I will plan, test, implement, verify, then review.", None),
        ],
    )

    run_deterministic_extractors(conn)

    records = conn.execute("select pattern_type, evidence_json from records order by pattern_type").fetchall()
    joined = "\n".join(row["evidence_json"] for row in records)
    assert "Hermes/CLI capabilities" not in joined
    assert "assessment=1" not in joined
    assert "tickets open on kanban" not in joined
    assert "Prompt saved" not in joined
    assert "Can we plan and verify" in joined


def test_active_task_list_handoff_is_filtered_from_ingest(tmp_path):
    fixture = tmp_path / "tasklist.jsonl"
    fixture.write_text(
        '{"role":"user","content":"[Your active task list was preserved across context compression]\\n- [>] pilot. Implement pilot."}\n'
        '{"role":"user","content":"Can we verify this?"}\n',
        encoding="utf-8",
    )
    conn = connect(tmp_path / "miner.db")
    init_db(conn)

    result = ingest_path(conn, fixture)

    assert result.turns_ingested == 1
    assert conn.execute("select text from turns").fetchone()["text"] == "Can we verify this?"


def test_duplicate_first_quote_is_deduped_even_with_different_context(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    quote = "What was the last work done?"
    _seed_session(
        conn,
        turns=[
            ("leon", quote, None),
            ("agent", "First answer.", None),
            ("leon", quote, None),
            ("agent", "Different answer.", None),
        ],
    )

    run_deterministic_extractors(conn)

    evidence_quotes = [row["evidence_json"] for row in conn.execute("select evidence_json from records")]
    assert sum(quote in evidence for evidence in evidence_quotes) == 1


def test_personal_summary_is_redacted_when_quote_is_suppressed(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, turns=[("leon", "Can you draw up an email to Garrit because I am on the market?", None)])
    run_deterministic_extractors(conn)

    report = write_pilot_report(conn, tmp_path / "pilot.md", run_id="personal", include_quotes=True)
    text = report.read_text(encoding="utf-8")

    assert "SUPPRESSED_PERSONAL_QUOTE" in text
    assert "Garrit" not in text
    assert "on the market" not in text
    assert "draw up an email" not in text


def test_pilot_report_does_not_duplicate_generic_example_section(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(conn, turns=[("leon", "Can we verify this?", None), ("agent", "I will plan, test, implement, and verify.", None)])
    run_deterministic_extractors(conn)

    report = write_pilot_report(conn, tmp_path / "pilot.md", run_id="report", include_quotes=True)
    text = report.read_text(encoding="utf-8")

    assert "## Examples by top pattern" in text
    assert "## Example records" not in text


def test_report_includes_llm_progress_counters(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    for sid in ("processed-zero", "processed-records", "excluded", "remaining"):
        _seed_session(conn, session_id=sid, turns=[("agent", "Should I verify this?", None)])
        conn.execute("update sessions set status='extracted' where session_id=?", (sid,))
    conn.execute(
        "insert into llm_session_runs(session_id, extractor_version, records_created) values ('processed-zero', 'local-qwen3-32b-q4km-v3', 0)"
    )
    conn.execute(
        "insert into llm_session_runs(session_id, extractor_version, records_created) values ('processed-records', 'local-qwen3-32b-q4km-v3', 2)"
    )
    conn.executemany(
        "insert into errors(session_id, error_class, payload_excerpt) values ('excluded', 'llm_extract_error', ?)",
        [("bad json",), ("bad json again",)],
    )
    conn.commit()

    report = write_pilot_report(conn, tmp_path / "pilot.md", run_id="progress")
    text = report.read_text(encoding="utf-8")

    assert "## LLM progress" in text
    assert "- local-qwen3-32b-q4km-v3 processed sessions: 2" in text
    assert "- local-qwen3-32b-q4km-v3 zero-record processed sessions: 1" in text
    assert "- local-qwen3-32b-q4km-v3 retry-cap excluded sessions: 1" in text
    assert "- local-qwen3-32b-q4km-v3 remaining under retry cap: 1" in text


def test_template_like_methodology_quotes_are_deduped_and_status_updates_demoted(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    _seed_session(
        conn,
        turns=[
            ("agent", "I'm invoking Fable for the BA-M3-03 read-only review now.", None),
            ("agent", "I'm invoking Fable for the BA-M3-04 read-only review now.", None),
            ("agent", "Full verification is green. I'm preparing the BA-M3-03 Fable read-only review packet with the diff.", None),
            ("agent", "Full verification is green. I'm preparing the BA-M3-04 Fable read-only review packet with the evidence.", None),
            ("agent", "Full backend, full frontend, and frontend production build all passed. I'll update the verification artifact with dogfood findings.", None),
            ("agent", "I’ll continue from the preserved task list: finish the synthetic persona, run an actual local browser/API dogfood, then only implement/refine gaps that the dogfood or Fable review exposes.", None),
            ("agent", "I’ll verify the scope compression test and then continue the dogfood.", None),
            ("agent", "I would work this as a controlled sequence: 1. Start Phase 0. 2. Keep downstream cards blocked. 3. Verify before review.", None),
            ("agent", "I will plan, test, implement, verify, then review.", None),
        ],
    )

    run_deterministic_extractors(conn)

    rows = conn.execute("select evidence_json, recommended_sink from records where stream='methodology' order by recommended_sink").fetchall()
    joined = "\n".join(row["evidence_json"] for row in rows)
    assert joined.count("I'm invoking Fable") == 1
    assert joined.count("Full verification is green") == 1
    assert any("controlled sequence" in row["evidence_json"] and row["recommended_sink"] == "skill_candidate" for row in rows)
    assert any("plan, test, implement" in row["evidence_json"] and row["recommended_sink"] == "skill_candidate" for row in rows)
    assert any("I'm invoking Fable" in row["evidence_json"] and row["recommended_sink"] == "report_only" for row in rows)
    assert not any("Full backend" in row["evidence_json"] and row["recommended_sink"] == "skill_candidate" for row in rows)
    assert not any("preserved task list" in row["evidence_json"] and row["recommended_sink"] == "skill_candidate" for row in rows)
    assert not any("scope compression test" in row["evidence_json"] and row["recommended_sink"] == "skill_candidate" for row in rows)


def test_brief_truncation_is_word_bounded_with_ellipsis():
    from leon_pattern_miner.extractors import _brief

    text = "ask fable to review the full diff tests and evidence bundle before proceeding"
    brief = _brief(text, max_chars=24)

    assert brief.endswith("…")
    assert brief == "ask fable to review the…"


def test_status_snapshot_counts_llm_progress(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    for sid in ("processed-zero", "excluded", "remaining"):
        _seed_session(conn, session_id=sid, turns=[("agent", "Should I verify this?", None)])
        conn.execute("update sessions set status='extracted' where session_id=?", (sid,))
    conn.execute(
        "insert into llm_session_runs(session_id, extractor_version, records_created) values ('processed-zero', 'local-qwen3-32b-q4km-v3', 0)"
    )
    conn.executemany(
        "insert into errors(session_id, error_class, payload_excerpt) values ('excluded', 'llm_extract_error', ?)",
        [("bad json",), ("bad json again",)],
    )
    conn.execute(
        """
        insert into records(
            record_id, session_id, stream, pattern_type, summary, evidence_json,
            actor, confidence, sensitivity, extractor, extractor_version, recommended_sink
        ) values (
            'non-llm-same-version', 'remaining', 'methodology', 'plan_spike_build_verify',
            'non-LLM record using a colliding version label', '[]', 'agent', 0.5, 'internal',
            'deterministic', 'local-qwen3-32b-q4km-v3', 'report_only'
        )
        """
    )
    conn.commit()

    snap = status_snapshot(conn)
    llm_progress = snap["llm_progress"]
    assert isinstance(llm_progress, dict)

    assert llm_progress["local-qwen3-32b-q4km-v3"] == {
        "processed_sessions": 1,
        "zero_record_processed_sessions": 1,
        "records_created": 0,
        "remaining_under_retry_cap": 1,
        "retry_cap_excluded_sessions": 1,
    }


def test_monitor_scripts_are_resilient_and_amortized():
    once = Path("scripts/monitor_once.sh").read_text(encoding="utf-8")
    loop = Path("scripts/run_full_corpus_monitor.sh").read_text(encoding="utf-8")

    assert "extract_status=" in once
    assert "report_status=" in once
    assert "monitor extract failed" in once
    assert 'LLM_BATCH="${LLM_BATCH:-25}"' in loop
