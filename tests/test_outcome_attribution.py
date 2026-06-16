import glob, json
from leon_pattern_miner.cie import (
    load_default_codebook, allowed_codes_for_family, pass_families,
    families_for_pass_strategy, render_cie_prompt_bundle, validate_cie_payload,
    build_session_windows,
)
from leon_pattern_miner.outcome_scoring import score_outcomes

OUTCOME_CODES = {"intent_stated", "delivery_result", "rework_cause"}

def _turn(idx, actor, text, sid="synthetic:s1"):
    return {"session_id": sid, "turn_id": f"{sid}:{idx}", "idx": idx, "actor": actor, "text": text}

def _src(text):
    return {"synthetic:s1:0": {"turn_id": "synthetic:s1:0", "actor": "leon", "text": text}}

def _payload(code, delivery, cause, quote, rel="A", cred=1, actor="leon"):
    return {"records": [{
        "codebook_code": code, "unit": "arc", "statement": "synthetic outcome record",
        "actor": actor, "source_reliability": rel, "info_credibility": cred,
        "facets": {"delivery": delivery, "cause": cause},
        "evidence": [{"turn_id": "synthetic:s1:0", "quote": quote}],
        "confidence": "high", "sensitivity": "internal",
    }]}

def test_family_registered():
    cb = load_default_codebook()
    assert allowed_codes_for_family("outcome_attribution", cb) == OUTCOME_CODES
    assert "outcome_attribution" in pass_families(cb)

def test_per_family_strategy_detects_outcome_window():
    win = build_session_windows([_turn(0, "leon",
        "the spec I gave was ambiguous so you built the wrong target and had to redo it")])[0]
    assert "outcome_attribution" in families_for_pass_strategy(win, "per_family")

def test_prompt_bundle_includes_outcome_cards_and_quote_sources():
    cb = load_default_codebook()
    win = build_session_windows([_turn(0, "leon", "redo it, the build was the wrong target")])[0]
    bundle = render_cie_prompt_bundle(win, family="outcome_attribution", codebook=cb)
    assert "rework_cause" in bundle.prompt and bundle.quote_sources

def test_validator_accepts_leon_caused_rework():
    text = "the spec I gave was ambiguous, redo it against the hermes board"
    valid, rejected = validate_cie_payload(
        _payload("rework_cause", "rework", "leon_instruction", "the spec I gave was ambiguous"),
        _src(text), family="outcome_attribution", quote_source_texts={"synthetic:s1:0": text})
    assert len(valid) == 1 and not rejected

def test_validator_rejects_rework_cause_without_real_cause():
    text = "redid the work"
    valid, rejected = validate_cie_payload(
        _payload("rework_cause", "rework", "none", "redid the work", rel="C", cred=3, actor="agent"),
        _src(text), family="outcome_attribution", quote_source_texts={"synthetic:s1:0": text})
    assert not valid and rejected

def test_validator_rejects_bad_enum():
    text = "we shipped it"
    valid, rejected = validate_cie_payload(
        _payload("delivery_result", "totally_done", "vibes", "we shipped it", rel="C", cred=3, actor="agent"),
        _src(text), family="outcome_attribution", quote_source_texts={"synthetic:s1:0": text})
    assert not valid and rejected

def test_validator_rejects_fabricated_quote():
    text = "do the thing"
    valid, rejected = validate_cie_payload(
        _payload("delivery_result", "landed", "none", "merged and shipped to prod", rel="C", cred=3, actor="agent"),
        _src(text), family="outcome_attribution", quote_source_texts={"synthetic:s1:0": text})
    assert not valid and rejected

# LOAD-BEARING: the feature exists to make this non-zero. Do not weaken.
def test_scorer_surfaces_leon_as_a_cause():
    records = [
        {"codebook_code": "rework_cause", "facets": {"delivery": "rework", "cause": "leon_instruction"}},
        {"codebook_code": "rework_cause", "facets": {"delivery": "failed", "cause": "leon_instruction"}},
        {"codebook_code": "rework_cause", "facets": {"delivery": "rework", "cause": "agent"}},
        {"codebook_code": "delivery_result", "facets": {"delivery": "landed", "cause": "none"}},
    ]
    s = score_outcomes(records)
    assert s["rework_total"] == 3
    assert s["leon_cause_fraction"] > 0.0
    assert s["top_cause"] == "leon_instruction"

def test_end_to_end_pipeline_runs_on_synthetic_fixture():
    cb = load_default_codebook()
    paths = sorted(glob.glob("tests/fixtures/outcome-v0/*.json"))
    assert paths, "synthetic outcome fixture missing"
    records = []
    for p in paths:
        sess = json.load(open(p))
        for win in build_session_windows(sess["turns"]):
            if "outcome_attribution" not in families_for_pass_strategy(win, "per_family"):
                continue
            bundle = render_cie_prompt_bundle(win, family="outcome_attribution", codebook=cb)
            payload = _stub_extract(bundle, win)  # deterministic, no model; quotes must be substrings of bundle.quote_sources
            src = {str(t["turn_id"]): t for t in win.turns}
            valid, _ = validate_cie_payload(payload, src, family="outcome_attribution",
                                             quote_source_texts=bundle.quote_sources)
            records.extend(valid)
    s = score_outcomes(records)
    assert s["n_outcome_records"] >= 1
    assert s["leon_cause_fraction"] > 0.0  # fixture is engineered with Leon-caused failures

def _stub_extract(bundle, win):
    # Implement: return a {"records":[...]} payload whose evidence quotes are exact substrings of
    # bundle.quote_sources, mirroring the engineered fixture (>=1 leon_instruction rework). No model call.
    for path in glob.glob("tests/fixtures/outcome-v0/*.json"):
        sess = json.load(open(path))
        if sess.get("session_id") != win.session_id:
            continue
        records = []
        for record in sess.get("expected_records", []):
            if all(item["quote"] in bundle.quote_sources.get(str(item["turn_id"]), "") for item in record["evidence"]):
                records.append(record)
        return {"records": records}
    return {"records": []}
