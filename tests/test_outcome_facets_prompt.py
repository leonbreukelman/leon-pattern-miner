from leon_pattern_miner.cie import (
    load_default_codebook, render_cie_prompt_bundle, build_session_windows,
    validate_cie_payload,
)

DELIVERY = ["landed", "partial", "rework", "failed", "unknown"]
CAUSES = ["leon_instruction", "agent", "tool", "environment", "none"]

def _win(text="the spec I gave was ambiguous so you built the wrong target and had to redo it"):
    return build_session_windows([{
        "session_id": "synthetic:s", "turn_id": "synthetic:s:0",
        "idx": 0, "actor": "leon", "text": text,
    }])[0]

def test_combined_prompt_documents_facets_schema():
    # JSON-key form ('"delivery"') only appears if rendered into the output schema,
    # not from the prose code-card definitions (which use the words unquoted).
    p = render_cie_prompt_bundle(_win(), family="all", codebook=load_default_codebook()).prompt
    assert '"delivery"' in p and '"cause"' in p
    assert all(v in p for v in DELIVERY)
    assert "leon_instruction" in p

def test_per_family_outcome_prompt_documents_facets():
    p = render_cie_prompt_bundle(_win(), family="outcome_attribution",
                                 codebook=load_default_codebook()).prompt
    assert '"delivery"' in p and '"cause"' in p
    assert all(v in p for v in DELIVERY)

def test_prompt_rules_mention_outcome_facets():
    pr = " ".join(load_default_codebook().get("prompt_rules", []))
    assert "delivery" in pr and "cause" in pr

def test_combined_prompt_shows_a_populated_outcome_fewshot():
    p = render_cie_prompt_bundle(_win(), family="all", codebook=load_default_codebook()).prompt
    # an outcome few-shot with filled facets must survive into the combined prompt
    assert "rework_cause" in p
    assert any(c in p for c in ("leon_instruction", "agent", "tool", "environment"))

# Regression: the gate that produced the 620 must still reject empty facets.
def test_validator_still_rejects_empty_facets():
    src = {"synthetic:s:0": {"turn_id": "synthetic:s:0", "actor": "leon", "text": "redo it, wrong target"}}
    payload = {"records": [{
        "codebook_code": "rework_cause", "unit": "arc", "statement": "x",
        "actor": "leon", "source_reliability": "A", "info_credibility": 1,
        "facets": {}, "evidence": [{"turn_id": "synthetic:s:0", "quote": "redo it"}],
        "confidence": "high", "sensitivity": "internal",
    }]}
    valid, rejected = validate_cie_payload(payload, src, family="outcome_attribution",
                                           quote_source_texts={"synthetic:s:0": "redo it, wrong target"})
    assert not valid and rejected

def test_validator_accepts_populated_facets():
    text = "the spec I gave was ambiguous, redo it"
    src = {"synthetic:s:0": {"turn_id": "synthetic:s:0", "actor": "leon", "text": text}}
    payload = {"records": [{
        "codebook_code": "rework_cause", "unit": "arc", "statement": "ambiguous spec caused rework",
        "actor": "leon", "source_reliability": "A", "info_credibility": 1,
        "facets": {"delivery": "rework", "cause": "leon_instruction"},
        "evidence": [{"turn_id": "synthetic:s:0", "quote": "the spec I gave was ambiguous"}],
        "confidence": "high", "sensitivity": "internal",
    }]}
    valid, rejected = validate_cie_payload(payload, src, family="outcome_attribution",
                                           quote_source_texts={"synthetic:s:0": text})
    assert len(valid) == 1 and not rejected
