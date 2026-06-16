from leon_pattern_miner.cie import (
    build_session_windows,
    families_for_window,
    init_cie_tables,
    render_cie_prompt,
    render_cie_prompt_bundle,
    run_cie_harness,
    validate_cie_payload,
)
from leon_pattern_miner.db import connect, init_db


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


def test_windowing_covers_long_sessions_with_overlap_and_no_prompt_overflow():
    turns = [
        {
            "turn_id": f"s:{idx}",
            "session_id": "s",
            "idx": idx,
            "actor": "leon" if idx % 2 == 0 else "agent",
            "text": ("verify this and do not skip evidence. " + str(idx) + " ") * 30,
            "tool_name": "",
        }
        for idx in range(120)
    ]

    windows = build_session_windows(turns, max_window_tokens=900, overlap_tokens=160)

    assert len(windows) > 3
    covered = {turn["idx"] for window in windows for turn in window.turns}
    assert covered == set(range(120))
    assert all(window.token_estimate <= 1050 for window in windows)
    assert any(
        set(windows[i].turn_indices).intersection(windows[i + 1].turn_indices)
        for i in range(len(windows) - 1)
    )


def test_prompt_uses_codebook_fewshots_and_stays_under_budget():
    turns = [
        {"turn_id": "s:0", "session_id": "s", "idx": 0, "actor": "leon", "text": "do not edit files or post to GitHub", "tool_name": ""},
        {"turn_id": "s:1", "session_id": "s", "idx": 1, "actor": "agent", "text": "I will only review and report.", "tool_name": ""},
    ]
    window = build_session_windows(turns, max_window_tokens=1000)[0]

    prompt = render_cie_prompt(window, family="authorization", max_prompt_tokens=2200)

    assert "authorization_limit" in prompt
    assert "Do not edit files or post to GitHub." in prompt
    assert "source_reliability" in prompt
    assert "Return strict JSON" in prompt
    assert len(prompt) // 4 <= 2200


def test_prompt_bundle_validates_against_exact_displayed_quote_source():
    raw_text = "Do not email leon@example.com.\n\nUse    Fable for the owner-facing review."
    turns = [
        {"turn_id": "s:0", "session_id": "s", "idx": 0, "actor": "leon", "text": raw_text, "tool_name": ""},
    ]
    window = build_session_windows(turns, max_window_tokens=1000)[0]

    bundle = render_cie_prompt_bundle(window, family="authorization_limit", max_prompt_tokens=2200)

    assert "leon@example.com" not in bundle.prompt
    assert "[REDACTED_EMAIL_1]" in bundle.prompt
    assert bundle.quote_sources["s:0"] == "Do not email [REDACTED_EMAIL_1]. Use Fable for the owner-facing review."
    payload = {
        "records": [
            {
                "codebook_code": "authorization_limit",
                "unit": "turn",
                "statement": "Leon forbids emailing the redacted address.",
                "actor": "leon",
                "source_reliability": "A",
                "info_credibility": 1,
                "evidence": [{"turn_id": "s:0", "quote": "Do not email [REDACTED_EMAIL_1]."}],
                "assumptions": [],
                "alternative_interpretations": [],
                "disconfirming_evidence": [],
                "falsifiers": [],
                "confidence": "high",
                "confidence_basis": "direct instruction",
                "sensitivity": "internal",
            }
        ]
    }

    valid, rejected = validate_cie_payload(
        payload,
        {"s:0": turns[0]},
        family="authorization_limit",
        quote_source_texts=bundle.quote_sources,
    )

    assert len(valid) == 1
    assert rejected == []


def test_model_routing_signal_routes_to_authorization_pass_not_dead_family():
    turns = [
        {"turn_id": "s:0", "session_id": "s", "idx": 0, "actor": "leon", "text": "Use Fable for strategy.", "tool_name": ""},
    ]
    window = build_session_windows(turns, max_window_tokens=1000)[0]

    assert families_for_window(window) == ["authorization_limit"]


def test_validate_rejects_unverifiable_quotes_and_keeps_valid_records():
    source_turns = {
        "s:0": {"actor": "leon", "text": "that was the wrong kanban, i was refering to the hermes kanban"},
        "s:1": {"actor": "agent", "text": "I corrected course."},
    }
    payload = {
        "records": [
            {
                "codebook_code": "correction_preference",
                "unit": "turn",
                "statement": "Leon corrected the target kanban.",
                "actor": "leon",
                "source_reliability": "A",
                "info_credibility": 1,
                "evidence": [{"turn_id": "s:0", "quote": "wrong kanban"}],
                "assumptions": ["The correction applies to the current task target."],
                "alternative_interpretations": [{"interpretation": "typo only", "why_less_likely": "explicit redirect"}],
                "disconfirming_evidence": [],
                "falsifiers": ["Later turn says Hermes kanban was not intended."],
                "confidence": "high",
                "confidence_basis": "direct user correction",
                "sensitivity": "internal",
            },
            {
                "codebook_code": "correction_preference",
                "unit": "turn",
                "statement": "Bad hallucinated quote.",
                "actor": "leon",
                "source_reliability": "A",
                "info_credibility": 1,
                "evidence": [{"turn_id": "s:0", "quote": "quote not present"}],
                "assumptions": ["x"],
                "alternative_interpretations": [],
                "disconfirming_evidence": [],
                "falsifiers": ["x"],
                "confidence": "low",
                "confidence_basis": "x",
                "sensitivity": "internal",
            },
        ]
    }

    valid, rejected = validate_cie_payload(payload, source_turns, family="correction_preference")

    assert len(valid) == 1
    assert valid[0]["quote_verified"] is True
    assert rejected[0]["reason"] == "quote_not_found"


def test_validate_rejects_model_routing_without_named_model_or_routing_language():
    source_turns = {
        "s:0": {"actor": "leon", "text": "Do not edit files or post to GitHub."},
    }
    payload = {
        "records": [
            {
                "codebook_code": "model_routing",
                "unit": "turn",
                "statement": "Leon limits edits and GitHub posts.",
                "actor": "leon",
                "source_reliability": "A",
                "info_credibility": 1,
                "evidence": [{"turn_id": "s:0", "quote": "Do not edit files or post to GitHub."}],
                "assumptions": ["The limit applies to this session."],
                "alternative_interpretations": [],
                "disconfirming_evidence": [],
                "falsifiers": ["A later turn authorizes edits."],
                "confidence": "medium",
                "confidence_basis": "direct quote",
                "sensitivity": "internal",
            }
        ]
    }

    valid, rejected = validate_cie_payload(payload, source_turns, family="authorization_limit")

    assert valid == []
    assert rejected[0]["reason"] == "model_routing_without_named_route"


def test_validate_rejects_source_reliability_a_for_tool_only_evidence():
    source_turns = {
        "s:0": {"actor": "tool", "text": "ERROR: command failed\nexit_code: 1"},
    }
    base_record = {
        "codebook_code": "verification_review",
        "unit": "turn",
        "statement": "The command failed during verification.",
        "actor": "tool",
        "info_credibility": 1,
        "evidence": [{"turn_id": "s:0", "quote": "ERROR: command failed"}],
        "assumptions": [],
        "alternative_interpretations": [],
        "disconfirming_evidence": [],
        "falsifiers": [],
        "confidence": "high",
        "confidence_basis": "tool output",
        "sensitivity": "internal",
    }

    valid, rejected = validate_cie_payload(
        {"records": [dict(base_record, source_reliability="A")]},
        source_turns,
        family="verification_review",
    )

    assert valid == []
    assert rejected[0]["reason"] == "source_reliability_a_without_direct_source"

    valid, rejected = validate_cie_payload(
        {"records": [dict(base_record, source_reliability="D")]},
        source_turns,
        family="verification_review",
    )

    assert len(valid) == 1
    assert rejected == []


def test_validate_caps_records_per_payload_to_prevent_code_spam():
    source_turns = {
        "s:0": {"actor": "leon", "text": "do not post this anywhere"},
    }
    base_record = {
        "codebook_code": "authorization_limit",
        "unit": "turn",
        "statement": "Leon forbids posting.",
        "actor": "leon",
        "source_reliability": "A",
        "info_credibility": 1,
        "evidence": [{"turn_id": "s:0", "quote": "do not post this anywhere"}],
        "assumptions": [],
        "alternative_interpretations": [],
        "disconfirming_evidence": [],
        "falsifiers": [],
        "confidence": "high",
        "confidence_basis": "direct quote",
        "sensitivity": "internal",
    }
    payload = {"records": [dict(base_record, statement=f"record {idx}") for idx in range(4)]}

    valid, rejected = validate_cie_payload(payload, source_turns, family="authorization_limit")

    assert len(valid) == 3
    assert rejected[-1]["reason"] == "too_many_records"


def test_runner_marks_zero_record_windows_processed(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    init_cie_tables(conn)
    _seed_session(
        conn,
        turns=[
            ("leon", "please verify this but do not post to GitHub", None),
            ("agent", "I will verify and report only.", None),
        ],
    )

    def fake_chat(prompt, *, base_url="http://unused", timeout=1):
        return {"json": {"records": []}, "masked_hits": 0}

    summary = run_cie_harness(
        conn,
        extractor_version="cie-test",
        max_sessions=1,
        chat_func=fake_chat,
        max_window_tokens=1200,
    )

    assert summary.sessions_processed == 1
    assert summary.window_runs >= 1
    assert summary.records_created == 0
    row = conn.execute("select status, records_created from cie_window_runs").fetchone()
    assert row["status"] == "processed"
    assert row["records_created"] == 0
