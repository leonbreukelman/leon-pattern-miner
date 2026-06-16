from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.extractors import run_deterministic_extractors


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


def test_same_quote_can_emit_distinct_deterministic_patterns_without_id_collision(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    quote = "Can we verify this? Actually, do not deploy without approval?"
    _seed_session(conn, turns=[("leon", quote, None)])

    run_deterministic_extractors(conn)

    rows = conn.execute("select pattern_type, extractor_version, evidence_json from records order by pattern_type").fetchall()
    patterns = {row["pattern_type"] for row in rows if quote in row["evidence_json"]}
    assert {"authorization_limit", "clarification_qa"}.issubset(patterns)
    assert {row["extractor_version"] for row in rows} == {"deterministic-v2"}
