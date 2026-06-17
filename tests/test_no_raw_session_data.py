import json, pathlib, re

REPO = pathlib.Path(__file__).resolve().parents[1]
SELF = pathlib.Path(__file__).name

# Real Hermes ids are timestamped or cron-worker. Synthetic teaching ids never use the hermes: namespace.
REAL_ID = re.compile(r"hermes:(?:\d{8}_\d+_[0-9a-f]+|cron_[0-9a-f]+_)")
ALLOWED_TURN_PREFIXES = ("synthetic:", "fixture:")
SCAN_EXT = {".json", ".jsonl", ".md", ".py", ".txt", ".yml", ".yaml", ".toml"}
ROOTS = ["src", "docs", "tests", "benchmark", "scripts", "README.md", "AGENTS.md"]

def _files():
    for root in ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        paths = [base] if base.is_file() else base.rglob("*")
        for p in paths:
            if p.is_file() and p.suffix.lower() in SCAN_EXT and p.name != SELF:
                yield p

def _iter_turn_ids(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "turn_id" and isinstance(v, str):
                yield v
            else:
                yield from _iter_turn_ids(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_turn_ids(it)

def test_no_real_hermes_session_ids_in_tracked_files():
    offenders = []
    for p in _files():
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in REAL_ID.finditer(txt):
            offenders.append(f"{p.relative_to(REPO)}: {m.group(0)}")
    assert not offenders, "real Hermes session ids present:\n" + "\n".join(sorted(set(offenders)))

def test_all_turn_ids_use_synthetic_or_fixture_prefix():
    bad = []
    for p in _files():
        if p.suffix.lower() not in {".json", ".jsonl"}:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for tid in _iter_turn_ids(data):
            if not str(tid).startswith(ALLOWED_TURN_PREFIXES):
                bad.append(f"{p.relative_to(REPO)}: {tid}")
    assert not bad, "turn_ids not using synthetic:/fixture::\n" + "\n".join(sorted(set(bad)))
