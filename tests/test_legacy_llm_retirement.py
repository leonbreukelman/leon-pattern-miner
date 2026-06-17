import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from leon_pattern_miner.cli import build_parser, main as cli_main
from leon_pattern_miner.db import connect, init_db
from leon_pattern_miner.report import write_pilot_report
from leon_pattern_miner.runner import status_snapshot


ROOT = Path(__file__).resolve().parents[1]
LEGACY_FLAG = "--use" + "-llm"
LEGACY_MODULE = "leon_pattern_miner." + "llm_extractors"
LEGACY_FILENAME = "llm" + "_extractors.py"
LEGACY_MODULE_TOKEN = "llm" + "_extractors"
LEGACY_RUNS_TABLE = "llm" + "_session_runs"
LEGACY_ERROR_CLASS = "llm" + "_extract_error"
LEGACY_DOC_PHRASE = "legacy/pilot"


def _seed_session(conn, session_id="s", turns=None):
    turns = turns or [("leon", "Please verify before deployment.", None), ("agent", "I will test and verify.", None)]
    conn.execute(
        "insert into sessions(session_id, source_path, format, turn_count, content_hash, status) values (?, ?, 'test', ?, ?, 'normalized')",
        (session_id, f"fixture:{session_id}", len(turns), session_id),
    )
    for idx, (actor, text, tool_name) in enumerate(turns):
        conn.execute(
            "insert into turns(turn_id, session_id, idx, actor, text, tool_name, char_offset_start, char_offset_end) values (?, ?, ?, ?, ?, ?, 0, ?)",
            (f"{session_id}:{idx}", session_id, idx, actor, text, tool_name, len(text)),
        )
    conn.commit()


def test_legacy_llm_cli_flag_is_retired_at_parse_time():
    parser = build_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["extract", LEGACY_FLAG])

    assert excinfo.value.code == 2


def test_legacy_llm_extractor_module_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(LEGACY_MODULE)


def test_active_source_and_steering_docs_do_not_reference_legacy_llm_path():
    active_paths = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "benchmark" / "README.md",
        ROOT / "docs" / "status" / "current-state.md",
        ROOT / "src" / "leon_pattern_miner" / "cli.py",
        ROOT / "src" / "leon_pattern_miner" / "db.py",
        ROOT / "src" / "leon_pattern_miner" / "runner.py",
        ROOT / "src" / "leon_pattern_miner" / "report.py",
        ROOT / "scripts" / "monitor_once.sh",
        ROOT / "scripts" / "run_pilot_resume.sh",
        ROOT / "scripts" / "run_full_corpus_monitor.sh",
    ]
    forbidden = [LEGACY_FLAG, LEGACY_FILENAME, LEGACY_MODULE_TOKEN, LEGACY_RUNS_TABLE, LEGACY_ERROR_CLASS, LEGACY_DOC_PHRASE]
    offenders = []
    for path in active_paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if path.name == "README.md" and line.startswith("If you previously used"):
                continue
            for token in forbidden:
                if token in line:
                    offenders.append(f"{path.relative_to(ROOT)} contains {token}")

    assert offenders == []


def test_status_snapshot_exposes_only_canonical_active_keys(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)

    assert set(status_snapshot(conn)) == {"sessions", "turns", "records", "errors", "work_items", "pilot_approved"}


def test_legacy_schema_leftovers_do_not_break_active_status_or_report(tmp_path):
    conn = connect(tmp_path / "miner.db")
    init_db(conn)
    conn.execute(
        """
        create table if not exists llm_session_runs(
            session_id text not null,
            extractor_version text not null,
            status text not null default 'processed',
            records_created integer not null default 0,
            processed_at text default (datetime('now')),
            primary key(session_id, extractor_version)
        )
        """
    )
    conn.execute("alter table errors add column extractor_version text")
    conn.execute(
        """
        insert into errors(session_id, error_class, extractor_version, payload_excerpt)
        values ('old', 'llm_extract_error', 'legacy-v', 'old local leftover')
        """
    )
    conn.commit()

    snap = status_snapshot(conn)
    report = write_pilot_report(conn, tmp_path / "report.md", run_id="legacy-leftover")
    text = report.read_text(encoding="utf-8")

    assert "llm_progress" not in snap
    assert "llm_session_runs" not in text
    assert "llm_extract_error" in text


def test_extract_cli_still_runs_deterministic_path_without_legacy_flags(tmp_path, capsys):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn)

    rc = cli_main(["--db", str(db), "extract"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "llm" not in out.lower()
    assert "records_created" in out
    assert connect(db).execute("select count(*) from records").fetchone()[0] > 0


def test_full_corpus_gate_still_blocks_without_pilot_approval(tmp_path, capsys):
    db = tmp_path / "miner.db"
    conn = connect(db)
    init_db(conn)
    _seed_session(conn)

    rc = cli_main(["--db", str(db), "extract", "--full-corpus"])

    assert rc == 2
    assert "requires approved 20-session pilot" in capsys.readouterr().out


def test_cie_xai_benchmark_path_still_parses_and_enforces_call_ceiling(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/run_benchmark.py",
            "--dataset",
            "benchmark/cie-extraction-v0",
            "--model",
            "grok-4.3",
            "--adapter",
            "xai",
            "--xai-reasoning-effort",
            "high",
            "--pass-strategy",
            "per_family",
            "--runs",
            "1",
            "--max-model-calls",
            "0",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "planned provider calls exceed --max-model-calls" in proc.stderr
