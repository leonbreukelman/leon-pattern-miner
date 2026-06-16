from collections import Counter

OUTCOME_CODES = {"intent_stated", "delivery_result", "rework_cause"}
CAUSES = ["leon_instruction", "agent", "tool", "environment"]


def score_outcomes(records: list[dict]) -> dict:
    outcome = [r for r in records if r.get("codebook_code") in OUTCOME_CODES]
    delivery = Counter(
        (r.get("facets") or {}).get("delivery")
        for r in outcome if r.get("codebook_code") == "delivery_result"
    )
    reworks = [r for r in outcome if r.get("codebook_code") == "rework_cause"]
    cause = Counter((r.get("facets") or {}).get("cause") for r in reworks)
    rework_total = len(reworks)
    leon = cause.get("leon_instruction", 0)
    return {
        "n_outcome_records": len(outcome),
        "delivery_distribution": dict(delivery),
        "cause_distribution": {c: cause.get(c, 0) for c in CAUSES},
        "rework_total": rework_total,
        "leon_cause_fraction": (leon / rework_total) if rework_total else 0.0,
        "top_cause": cause.most_common(1)[0][0] if cause else "none",
    }
