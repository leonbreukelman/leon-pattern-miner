import json
import re

from leon_pattern_miner.cie import init_cie_tables, run_cie_harness
from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.ingest import IngestResult
from leon_pattern_miner.llm import ProviderCallBudget
import leon_pattern_miner.mine as mine_module
from leon_pattern_miner.mine import run_mine_cycle, write_schedule_templates
from leon_pattern_miner.report import write_findings_report


def _seed_session(conn, session_id, text):
    conn.execute(
        "insert into sessions(session_id, source_path, format, turn_count, content_hash, status, started_at) values (?, ?, 'test', 1, ?, 'normalized', 1)",
        (session_id, f"fixture:{session_id}", session_id),
    )
    conn.execute(
        "insert into turns(turn_id, session_id, idx, actor, text, tool_name, char_offset_start, char_offset_end) values (?, ?, 0, 'leon', ?, '', 0, ?)",
        (f"{session_id}:0", session_id, text, len(text)),
    )
    conn.commit()


def _record_for_prompt(prompt, *, statement="Leon requires evidence-backed verification.", quote="please verify this"):
    turn_id = re.search(r"turn_id=([^\s]+)", prompt).group(1)
    return {
        "codebook_code": "verification_review",
        "unit": "turn",
        "statement": statement,
        "actor": "leon",
        "source_reliability": "A",
        "info_credibility": 1,
        "evidence": [{"turn_id": turn_id, "quote": quote}],
        "assumptions": [],
        "alternative_interpretations": [],
        "disconfirming_evidence": [],
        "falsifiers": [],
        "confidence": "high",
        "confidence_basis": "synthetic fixture",
        "sensitivity": "internal",
    }


def _make_state_db(path):
    source = connect(path)
    source.executescript(
        """
        create table sessions(id text primary key, source text not null, title text, started_at real not null, message_count integer default 0);
        create table messages(id integer primary key, session_id text not null, role text not null, content text, tool_name text, timestamp real not null);
        insert into sessions(id, source, title, started_at, message_count) values
          ('s1','cli','Newest useful', 20, 1),
          ('s2','cli','Older useful', 10, 1),
          ('s3','cli','leon-pattern-miner meta session', 30, 1);
        insert into messages(session_id, role, content, tool_name, timestamp) values
          ('s1','user','please verify this one', null, 21),
          ('s2','user','please verify this two', null, 11),
          ('s3','user','please verify pattern miner itself', null, 31);
        """
    )
    source.commit()
    return source


def test_cie_records_dedup_exact_normalized_duplicates_increment_occurrence_count(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    init_cie_tables(conn)
    _seed_session(conn, "fixture:s1", "please verify this before reporting")
    _seed_session(conn, "fixture:s2", "please verify this before reporting")
    calls = 0

    def fake_chat(prompt, **kwargs):
        nonlocal calls
        calls += 1
        statement = "  LEON requires evidence-backed verification!!!  " if calls == 1 else "leon requires evidence-backed verification"
        return {"json": {"records": [_record_for_prompt(prompt, statement=statement)]}}

    summary = run_cie_harness(
        conn,
        extractor_version="fixture-dedup",
        session_ids=["fixture:s1", "fixture:s2"],
        chat_func=fake_chat,
        combined_pass=True,
    )

    row = conn.execute("select statement, occurrence_count, dedup_key from cie_records").fetchone()
    assert summary.records_created == 1
    assert conn.execute("select count(*) from cie_records").fetchone()[0] == 1
    assert row["occurrence_count"] == 2
    assert row["dedup_key"]


def test_cie_records_dedup_keeps_distinct_findings_even_with_same_quote(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    init_cie_tables(conn)
    _seed_session(conn, "fixture:s1", "please verify this before reporting")

    def fake_chat(prompt, **kwargs):
        return {
            "json": {
                "records": [
                    _record_for_prompt(prompt, statement="Leon requires verification."),
                    _record_for_prompt(prompt, statement="Leon requires review before handoff."),
                ]
            }
        }

    summary = run_cie_harness(
        conn,
        extractor_version="fixture-distinct",
        session_ids=["fixture:s1"],
        chat_func=fake_chat,
        combined_pass=True,
    )

    assert summary.records_created == 2
    rows = conn.execute("select statement, occurrence_count from cie_records order by statement").fetchall()
    assert [row["occurrence_count"] for row in rows] == [1, 1]
    assert len(rows) == 2


def test_register_integrity_invalid_quotes_are_rejected_not_inserted(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    init_cie_tables(conn)
    _seed_session(conn, "fixture:s1", "please verify this before reporting")

    def fake_chat(prompt, **kwargs):
        return {"json": {"records": [_record_for_prompt(prompt, quote="not in source")]}}

    summary = run_cie_harness(
        conn,
        extractor_version="fixture-invalid",
        session_ids=["fixture:s1"],
        chat_func=fake_chat,
        combined_pass=True,
    )

    assert summary.records_created == 0
    assert summary.records_rejected == 1
    assert conn.execute("select count(*) from cie_records").fetchone()[0] == 0
    assert conn.execute("select rejection_cause from cie_rejections").fetchone()["rejection_cause"] == "quote_not_found"


def test_mine_cursor_idempotency_and_processed_session_replay(tmp_path):
    state = tmp_path / "state.db"
    _make_state_db(state)
    conn = connect(tmp_path / "miner.db")
    provider_requests = 0

    def fake_chat(prompt, **kwargs):
        nonlocal provider_requests
        provider_requests += 1
        turn_id = re.search(r"turn_id=([^\s]+)", prompt).group(1)
        quote = re.search(r"please verify this (?:one|two)", prompt).group(0)
        record = _record_for_prompt(prompt, quote=quote)
        record["evidence"] = [{"turn_id": turn_id, "quote": quote}]
        return {"json": {"records": [record]}}

    first = run_mine_cycle(
        conn,
        state_db=state,
        limit=2,
        chat_func=fake_chat,
        extractor_version="fixture-mine",
        report_path=tmp_path / "findings.md",
    )
    rows_after_first = conn.execute("select count(*) from cie_records").fetchone()[0]
    assert first["ingest"]["selected_sessions"] == 2
    assert first["cursor"]["before"] is None
    assert first["cursor"]["after"] == 20.0
    assert first["summary"]["records_created"] == 2
    assert provider_requests == 2

    second = run_mine_cycle(
        conn,
        state_db=state,
        limit=2,
        chat_func=fake_chat,
        extractor_version="fixture-mine",
        report_path=tmp_path / "findings.md",
    )
    assert second["ingest"]["selected_sessions"] == 0
    assert second["summary"]["window_runs"] == 0
    assert conn.execute("select count(*) from cie_records").fetchone()[0] == rows_after_first
    assert provider_requests == 2

    conn.execute("update miner_state set value='0' where key='last_processed_session_started_at'")
    conn.commit()
    replay = run_mine_cycle(
        conn,
        state_db=state,
        limit=2,
        chat_func=fake_chat,
        extractor_version="fixture-mine",
        report_path=tmp_path / "findings.md",
    )
    assert replay["ingest"]["selected_sessions"] == 2
    assert replay["cycle"]["planned_prompts"] == 0
    assert replay["summary"]["records_created"] == 0
    assert conn.execute("select count(*) from cie_records").fetchone()[0] == rows_after_first
    assert provider_requests == 2


def test_mine_cost_cap_stops_cycle_before_second_provider_request(tmp_path):
    state = tmp_path / "state.db"
    _make_state_db(state)
    conn = connect(tmp_path / "miner.db")
    budget = ProviderCallBudget(max_calls=10, cost_cap_usd=0.001, cost_estimate_model="grok-4.3")
    provider_requests = 0

    def capped_chat(prompt, **kwargs):
        nonlocal provider_requests
        budget.consume()
        provider_requests += 1
        budget.record_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "reasoning_tokens": 1,
                "total_tokens": 12,
                "cost_in_usd_ticks": 20_000_000,
                "cost_ticks_present": True,
            },
            json_parse_ok=True,
            attempt=1,
        )
        turn_id = re.search(r"turn_id=([^\s]+)", prompt).group(1)
        quote = re.search(r"please verify this (?:one|two)", prompt).group(0)
        record = _record_for_prompt(prompt, quote=quote)
        record["evidence"] = [{"turn_id": turn_id, "quote": quote}]
        return {"json": {"records": [record]}}

    result = run_mine_cycle(
        conn,
        state_db=state,
        limit=2,
        chat_func=capped_chat,
        provider_budget=budget,
        extractor_version="fixture-cap",
        report_path=tmp_path / "findings.md",
    )

    assert provider_requests == 1
    assert result["summary"]["budget_exhausted"] is True
    assert result["provider_usage"]["cost_cap_breached"] is True
    assert result["provider_usage"]["calls_made"] == 1


def test_mine_cursor_does_not_advance_when_ingest_reports_errors(tmp_path, monkeypatch):
    conn = connect(tmp_path / "miner.db")

    def fake_ingest(*args, **kwargs):
        return IngestResult(
            errors=1,
            selected_sessions=1,
            session_ids=("fixture:missing",),
            max_started_at=99.0,
        )

    monkeypatch.setattr(mine_module, "ingest_hermes_state_db", fake_ingest)

    result = run_mine_cycle(
        conn,
        state_db=tmp_path / "state.db",
        limit=1,
        chat_func=lambda prompt, **kwargs: {"json": {"records": []}},
        report_path=tmp_path / "findings.md",
    )

    assert result["cursor"] == {
        "key": "last_processed_session_started_at",
        "before": None,
        "after": None,
        "advanced": False,
    }


def test_findings_report_groups_by_family_and_sorts_by_frequency(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    init_cie_tables(conn)
    _seed_session(conn, "fixture:s1", "please verify this before reporting")

    def fake_chat(prompt, **kwargs):
        return {"json": {"records": [_record_for_prompt(prompt, statement="Frequent finding")]}}

    run_cie_harness(
        conn,
        extractor_version="fixture-report-a",
        session_ids=["fixture:s1"],
        chat_func=fake_chat,
    )
    conn.execute("update cie_records set occurrence_count=3 where statement='Frequent finding'")
    conn.execute(
        """
        insert into cie_records(record_id, session_id, window_id, extractor_version, family, codebook_code, unit,
          statement, actor, source_reliability, info_credibility, confidence, sensitivity, evidence_json,
          facets_json, assumptions_json, alternatives_json, disconfirming_json, falsifiers_json, quote_verified,
          dedup_key, occurrence_count, first_seen, last_seen)
        values ('manual-1','fixture:s1','w','v','verification_review','verification_review','turn',
          'Rare verification','leon','A',1,'high','internal','[]','{}','[]','[]','[]','[]',1,
          'manual-key',1,datetime('now'),datetime('now'))
        """
    )
    conn.execute(
        """
        insert into cie_records(record_id, session_id, window_id, extractor_version, family, codebook_code, unit,
          statement, actor, source_reliability, info_credibility, confidence, sensitivity, evidence_json,
          facets_json, assumptions_json, alternatives_json, disconfirming_json, falsifiers_json, quote_verified,
          dedup_key, occurrence_count, first_seen, last_seen)
        values ('manual-2','fixture:s1','w','v','authorization_limit','authorization_limit','turn',
          'Authorization finding','leon','A',1,'high','internal','[]','{}','[]','[]','[]','[]',1,
          'manual-key-2',1,datetime('now'),datetime('now'))
        """
    )
    conn.commit()

    path = write_findings_report(conn, tmp_path / "findings.md")
    text = path.read_text()

    assert "## verification_review" in text
    assert "## authorization_limit" in text
    assert text.index("Frequent finding") < text.index("Rare verification")
    assert "| statement | count | last_seen |" in text


def test_schedule_templates_are_written_but_not_enabled(tmp_path):
    out = write_schedule_templates(
        tmp_path,
        mine_command="cd /repo && uv run miner --db runtime/miner.db mine --cost-cap-usd 10",
    )

    assert {p.name for p in out} == {
        "hermes-cron-mine-nightly.json",
        "leon-pattern-miner-mine.service",
        "leon-pattern-miner-mine.timer",
        "README.md",
    }
    text = (tmp_path / "README.md").read_text()
    assert "not enabled" in text.lower()
    assert "systemctl --user enable" in text
    cron = json.loads((tmp_path / "hermes-cron-mine-nightly.json").read_text())
    assert cron["schedule"] == "0 3 * * *"
    assert "--cost-cap-usd 10" in cron["prompt"]
