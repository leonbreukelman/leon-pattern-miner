from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .cie import (
    DEFAULT_MAX_PROMPT_TOKENS,
    build_session_windows,
    families_for_pass_strategy,
    render_cie_prompt_bundle,
    validate_cie_payload,
)
from .cie_recall import Record, score_recall


@dataclass(frozen=True)
class BenchmarkDataset:
    root: Path
    manifest: dict[str, Any]
    sessions: dict[str, dict[str, Any]]
    gold: dict[str, list[dict[str, Any]]]
    baseline: dict[str, list[dict[str, Any]]]
    file_by_session: dict[str, str]
    bucket_by_session: dict[str, str]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(path: str | Path) -> BenchmarkDataset:
    """Load a frozen MinerMark dataset from benchmark/cie-extraction-v0 style files."""
    root = Path(path)
    manifest = _read_json(root / "manifest.json")
    sessions: dict[str, dict[str, Any]] = {}
    gold: dict[str, list[dict[str, Any]]] = {}
    baseline: dict[str, list[dict[str, Any]]] = {}
    file_by_session: dict[str, str] = {}
    bucket_by_session: dict[str, str] = {}

    for entry in manifest.get("entries", []):
        session_id = entry["session_id"]
        stem = entry["file"]
        file_by_session[session_id] = stem
        bucket_by_session[session_id] = entry.get("bucket", "unknown")

    for session_path in sorted((root / "sessions").glob("*.json")):
        payload = _read_json(session_path)
        session_id = payload["session_id"]
        sessions[session_id] = payload
        file_by_session.setdefault(session_id, session_path.stem)
        bucket_by_session.setdefault(session_id, payload.get("bucket", "unknown"))

    for gold_path in sorted((root / "gold").glob("*.json")):
        payload = _read_json(gold_path)
        gold[payload["session_id"]] = list(payload.get("records", []))

    for baseline_path in sorted((root / "baseline_qwen").glob("*.json")):
        payload = _read_json(baseline_path)
        baseline[payload["session_id"]] = list(payload.get("records", []))

    return BenchmarkDataset(
        root=root,
        manifest=manifest,
        sessions=sessions,
        gold=gold,
        baseline=baseline,
        file_by_session=file_by_session,
        bucket_by_session=bucket_by_session,
    )


def integrity_report(dataset: BenchmarkDataset) -> dict[str, Any]:
    """Return deterministic integrity checks for frozen session/gold evidence links."""
    turns_by_session = {
        session_id: {turn["turn_id"]: turn for turn in payload.get("turns", [])}
        for session_id, payload in dataset.sessions.items()
    }
    missing: list[dict[str, str]] = []
    quote_mismatches: list[dict[str, str]] = []
    evidence_count = 0
    for session_id, records in dataset.gold.items():
        known_turns = turns_by_session.get(session_id, {})
        for record in records:
            for evidence in record.get("evidence") or []:
                evidence_count += 1
                turn_id = evidence.get("turn_id") if isinstance(evidence, dict) else None
                quote = str(evidence.get("quote") or "") if isinstance(evidence, dict) else ""
                if turn_id not in known_turns:
                    missing.append({"session_id": session_id, "turn_id": str(turn_id)})
                    continue
                if quote and quote not in str(known_turns[turn_id].get("text") or ""):
                    quote_mismatches.append({"session_id": session_id, "turn_id": str(turn_id)})
    totals = dataset.manifest.get("totals", {})
    observed = {
        "sessions": len(dataset.sessions),
        "turns": sum(len(payload.get("turns", [])) for payload in dataset.sessions.values()),
        "gold_findings": sum(len(records) for records in dataset.gold.values()),
        "qwen_baseline_findings": sum(len(records) for records in dataset.baseline.values()),
    }
    manifest_mismatches = {
        key: {"manifest": totals.get(key), "observed": observed[key]}
        for key in observed
        if totals.get(key) != observed[key]
    }
    return {
        "sessions": len(dataset.sessions),
        "gold_files": len(dataset.gold),
        "baseline_files": len(dataset.baseline),
        "gold_evidence_count": evidence_count,
        "missing_evidence_turn_ids": missing,
        "gold_quote_mismatches": quote_mismatches,
        "manifest_mismatches": manifest_mismatches,
    }


def assert_dataset_integrity(dataset: BenchmarkDataset) -> dict[str, Any]:
    report = integrity_report(dataset)
    if report["missing_evidence_turn_ids"]:
        raise ValueError(f"missing evidence turn_id links: {report['missing_evidence_turn_ids'][:3]}")
    if report["gold_quote_mismatches"]:
        raise ValueError(f"gold quote mismatches: {report['gold_quote_mismatches'][:3]}")
    if report["manifest_mismatches"]:
        raise ValueError(f"manifest totals mismatch: {report['manifest_mismatches']}")
    return report


def _record_quote(record: Mapping[str, Any]) -> str:
    evidence = record.get("evidence")
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
        return str(evidence[0].get("quote") or "")
    return ""


def _to_scoring_records(records_by_session: Mapping[str, list[dict[str, Any]]]) -> list[Record]:
    out: list[Record] = []
    for session_id, records in records_by_session.items():
        for record in records:
            code = record.get("codebook_code")
            if isinstance(code, str) and code:
                out.append(Record(session_id=session_id, codebook_code=code, quote=_record_quote(record)))
    return out


def _agreement_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    out = dict(metrics)
    precision = out.pop("precision")
    out["agreement_with_opus"] = precision
    out["recall_ci95"] = wilson_ci(out["matched"], out["gold_total"])
    out["agreement_ci95"] = wilson_ci(out["matched"], out["candidate_total"])
    return out


def score_predictions(
    dataset: BenchmarkDataset,
    predictions: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Score candidate predictions against frozen Opus gold, code-level and quote-strict."""
    gold_records = _to_scoring_records(dataset.gold)
    candidate_records = _to_scoring_records(predictions)
    code_level = score_recall(gold=gold_records, candidate=candidate_records)
    quote_strict = score_recall(
        gold=gold_records,
        candidate=candidate_records,
        require_quote_overlap=True,
    )
    bucket_scores: dict[str, dict[str, Any]] = {}
    for bucket in ("short", "medium", "long"):
        session_ids = {
            session_id
            for session_id, session_bucket in dataset.bucket_by_session.items()
            if session_bucket == bucket
        }
        bucket_gold = {sid: dataset.gold.get(sid, []) for sid in session_ids}
        bucket_pred = {sid: predictions.get(sid, []) for sid in session_ids}
        bucket_scores[bucket] = _agreement_metrics(
            score_recall(
                gold=_to_scoring_records(bucket_gold),
                candidate=_to_scoring_records(bucket_pred),
            )
        )
    return {
        "code_level": _agreement_metrics(code_level),
        "quote_strict": _agreement_metrics(quote_strict),
        "buckets": bucket_scores,
    }


def score_baseline(dataset: BenchmarkDataset) -> dict[str, Any]:
    return score_predictions(dataset, dataset.baseline)


def _resolved_window_params(
    dataset: BenchmarkDataset,
    *,
    max_window_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> tuple[int, int]:
    window_params = dataset.manifest.get("window_params", {})
    return (
        int(max_window_tokens or window_params.get("max_window_tokens", 3500)),
        int(overlap_tokens if overlap_tokens is not None else window_params.get("overlap_tokens", 600)),
    )


def estimate_candidate_prompt_count(
    dataset: BenchmarkDataset,
    *,
    runs: int = 1,
    max_window_tokens: int | None = None,
    overlap_tokens: int | None = None,
    pass_strategy: str = "per_family",
) -> int:
    """Estimate benchmark prompt count for budget gates before live provider calls."""
    if pass_strategy not in {"per_family", "combined"}:
        raise ValueError("pass_strategy must be 'per_family' or 'combined'")
    max_window_tokens, overlap_tokens = _resolved_window_params(
        dataset,
        max_window_tokens=max_window_tokens,
        overlap_tokens=overlap_tokens,
    )
    prompts = 0
    for session_payload in dataset.sessions.values():
        session_id = str(session_payload.get("session_id") or "")
        turns = []
        for raw_turn in session_payload.get("turns", []):
            turn = dict(raw_turn)
            if session_id and "session_id" not in turn:
                turn["session_id"] = session_id
            turns.append(turn)
        for window in build_session_windows(
            turns,
            max_window_tokens=max_window_tokens,
            overlap_tokens=overlap_tokens,
        ):
            prompts += len(families_for_pass_strategy(window, pass_strategy))
    return prompts * max(0, int(runs))


def wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return value.strip("-") or "model"


def _record_for_output(record: dict[str, Any], *, window_id: str, family: str) -> dict[str, Any]:
    out = dict(record)
    out.setdefault("window_id", window_id)
    out.setdefault("family", family)
    return out


def _extract_session_predictions(
    session_payload: Mapping[str, Any],
    *,
    chat_func: Callable[..., dict[str, Any]],
    model_name: str,
    base_url: str,
    max_window_tokens: int,
    overlap_tokens: int,
    max_prompt_tokens: int,
    timeout: int,
    llm_max_tokens: int,
    pass_strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_id = str(session_payload.get("session_id") or "")
    turns = []
    for raw_turn in session_payload.get("turns", []):
        turn = dict(raw_turn)
        if session_id and "session_id" not in turn:
            turn["session_id"] = session_id
        turns.append(turn)
    source_turns = {turn["turn_id"]: turn for turn in turns}
    windows = build_session_windows(
        turns,
        max_window_tokens=max_window_tokens,
        overlap_tokens=overlap_tokens,
    )
    records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "windows": len(windows),
        "records_rejected": 0,
        "errors": 0,
        "pass_strategy": pass_strategy,
        "window_passes": 0,
        "no_signal_windows": 0,
    }
    for window in windows:
        families = families_for_pass_strategy(window, pass_strategy)
        if not families:
            stats["no_signal_windows"] += 1
            continue
        for family in families:
            stats["window_passes"] += 1
            prompt_bundle = render_cie_prompt_bundle(window, family=family, max_prompt_tokens=max_prompt_tokens)
            try:
                response = chat_func(
                    prompt_bundle.prompt,
                    base_url=base_url,
                    timeout=timeout,
                    max_tokens=llm_max_tokens,
                    model=model_name,
                )
                payload = response.get("json", response)
                response_model_ids = response.get("model_ids")
                if isinstance(response_model_ids, list):
                    stats.setdefault("model_ids", [])
                    stats["model_ids"].extend(str(model_id) for model_id in response_model_ids)
                valid, rejected = validate_cie_payload(
                    payload,
                    source_turns,
                    family=family,
                    quote_source_texts=prompt_bundle.quote_sources,
                )
                stats["records_rejected"] += len(rejected)
                records.extend(
                    _record_for_output(record, window_id=window.window_id, family=family)
                    for record in valid
                )
            except Exception:
                stats["errors"] += 1
    return records, stats


def _load_predictions_from_dir(pred_dir: Path) -> dict[str, list[dict[str, Any]]]:
    predictions: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(pred_dir.glob("*.json")):
        payload = _read_json(path)
        predictions[payload["session_id"]] = list(payload.get("records", []))
    return predictions


def _mean_sd(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "sd": 0.0}
    return {
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _transport_stats(session_stats: Mapping[str, Any]) -> dict[str, Any]:
    windows = 0
    errors = 0
    rejected = 0
    for stats in session_stats.values():
        if not isinstance(stats, Mapping):
            continue
        windows += int(stats.get("window_passes") or stats.get("windows") or 0)
        errors += int(stats.get("errors") or 0)
        rejected += int(stats.get("records_rejected") or 0)
    valid_json_windows = max(0, windows - errors)
    return {
        "windows": windows,
        "error_windows": errors,
        "valid_json_windows": valid_json_windows,
        "records_rejected": rejected,
        "error_window_rate": errors / windows if windows else 0.0,
        "valid_json_window_rate": valid_json_windows / windows if windows else 0.0,
    }


def _summarise_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "recall": _mean_sd([run["code_level"]["recall"] for run in runs]),
        "quote_strict_recall": _mean_sd([run["quote_strict"]["recall"] for run in runs]),
        "agreement_with_opus": _mean_sd(
            [run["code_level"]["agreement_with_opus"] for run in runs]
        ),
        "f1": _mean_sd([run["code_level"]["f1"] for run in runs]),
        "transport_error_window_rate": _mean_sd(
            [run.get("transport", {}).get("error_window_rate", 0.0) for run in runs]
        ),
        "transport_valid_json_window_rate": _mean_sd(
            [run.get("transport", {}).get("valid_json_window_rate", 0.0) for run in runs]
        ),
    }


def run_candidate(
    dataset: BenchmarkDataset,
    *,
    output_dir: str | Path,
    model_name: str,
    chat_func: Callable[..., dict[str, Any]],
    runs: int = 3,
    base_url: str = "http://127.0.0.1:8080",
    max_window_tokens: int | None = None,
    overlap_tokens: int | None = None,
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
    timeout: int = 300,
    llm_max_tokens: int = 4096,
    pass_strategy: str = "per_family",
    threshold: float | None = None,
    served_model_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run a candidate model over frozen sessions and write predictions + scorecards."""
    if pass_strategy not in {"per_family", "combined"}:
        raise ValueError("pass_strategy must be 'per_family' or 'combined'")
    output_root = Path(output_dir)
    integrity = assert_dataset_integrity(dataset)
    output_root.mkdir(parents=True, exist_ok=True)
    max_window_tokens, overlap_tokens = _resolved_window_params(
        dataset,
        max_window_tokens=max_window_tokens,
        overlap_tokens=overlap_tokens,
    )

    run_scores: list[dict[str, Any]] = []
    run_details: list[dict[str, Any]] = []
    observed_model_ids = set(served_model_ids or [])
    for run_idx in range(1, runs + 1):
        run_dir = output_root / f"run_{run_idx:02d}"
        pred_dir = run_dir / "predictions"
        pred_dir.mkdir(parents=True, exist_ok=True)
        session_stats: dict[str, Any] = {}
        for session_id, session_payload in dataset.sessions.items():
            records, stats = _extract_session_predictions(
                session_payload,
                chat_func=chat_func,
                model_name=model_name,
                base_url=base_url,
                max_window_tokens=max_window_tokens,
                overlap_tokens=overlap_tokens,
                max_prompt_tokens=max_prompt_tokens,
                timeout=timeout,
                llm_max_tokens=llm_max_tokens,
                pass_strategy=pass_strategy,
            )
            stem = dataset.file_by_session.get(session_id, session_id.replace(":", "__"))
            (pred_dir / f"{stem}.json").write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "extractor": model_name,
                        "run": run_idx,
                        "records": records,
                        "stats": stats,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session_stats[session_id] = stats
            model_ids = stats.get("model_ids")
            if isinstance(model_ids, list):
                observed_model_ids.update(str(model_id) for model_id in model_ids)
        predictions = _load_predictions_from_dir(pred_dir)
        score = score_predictions(dataset, predictions)
        score["run"] = run_idx
        score["session_stats"] = session_stats
        score["transport"] = _transport_stats(session_stats)
        run_scores.append(score)
        run_details.append({"run": run_idx, "predictions_dir": str(pred_dir), "score": score})
        (run_dir / "scorecard.json").write_text(
            json.dumps(score, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    result = {
        "model": model_name,
        "served_model_ids": sorted(observed_model_ids),
        "dataset": str(dataset.root),
        "dataset_integrity": integrity,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_requested": runs,
        "window_params": {
            "max_window_tokens": max_window_tokens,
            "overlap_tokens": overlap_tokens,
            "max_prompt_tokens": max_prompt_tokens,
            "llm_max_tokens": llm_max_tokens,
            "pass_strategy": pass_strategy,
        },
        "threshold": threshold,
        "baseline": score_baseline(dataset),
        "runs": run_scores,
        "summary": _summarise_runs(run_scores),
        "artifacts": run_details,
    }
    if threshold is not None:
        first_gold_total = run_scores[0]["code_level"]["gold_total"] if run_scores else 0
        result["recall_pass"] = (
            first_gold_total > 0 and result["summary"]["recall"]["mean"] >= threshold
        )
    (output_root / "scorecard.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_root / "scorecard.md").write_text(_render_scorecard(result), encoding="utf-8")
    return result


def _fmt_rate(value: float) -> str:
    return f"{value:.3f}"


def _fmt_ci(ci: tuple[float, float] | list[float]) -> str:
    return f"[{float(ci[0]):.3f}, {float(ci[1]):.3f}]"


def _render_metric_row(label: str, metrics: Mapping[str, Any]) -> str:
    return (
        f"| {label} | {metrics['matched']} / {metrics['gold_total']} | "
        f"{_fmt_rate(metrics['recall'])} | {_fmt_ci(metrics['recall_ci95'])} | "
        f"{_fmt_rate(metrics['agreement_with_opus'])} | "
        f"{_fmt_ci(metrics['agreement_ci95'])} | {_fmt_rate(metrics['f1'])} |"
    )


def _render_scorecard(result: Mapping[str, Any]) -> str:
    lines = [
        f"# MinerMark scorecard — {result['model']}",
        "",
        "## Read this first",
        "- Opus is a strong reference, not ground truth; this measures agreement-with-Opus.",
        "- Code-level matching is a lenient upper bound; quote-strict recall is reported too.",
        "- The 4090 is not run-to-run deterministic; use runs >= 3 and read mean ± sd.",
        "- The frozen v0 set is 15 conversations / 51 gold findings, so this is directional.",
        "- Circularity: the Qwen baseline had an Opus-few-shot home advantage.",
        "",
        "## Summary over runs",
        f"- served model ids observed: {', '.join(result.get('served_model_ids') or ['(not recorded)'])}",
        f"- runs: {result['runs_requested']}",
        f"- pass strategy: {result.get('window_params', {}).get('pass_strategy', 'unknown')}",
        f"- recall mean ± sd: {_fmt_rate(result['summary']['recall']['mean'])} ± {_fmt_rate(result['summary']['recall']['sd'])}",
        f"- quote-strict recall mean ± sd: {_fmt_rate(result['summary']['quote_strict_recall']['mean'])} ± {_fmt_rate(result['summary']['quote_strict_recall']['sd'])}",
        f"- agreement-with-Opus mean ± sd: {_fmt_rate(result['summary']['agreement_with_opus']['mean'])} ± {_fmt_rate(result['summary']['agreement_with_opus']['sd'])}",
        f"- transport error-window rate mean ± sd: {_fmt_rate(result['summary']['transport_error_window_rate']['mean'])} ± {_fmt_rate(result['summary']['transport_error_window_rate']['sd'])}",
        f"- transport valid-JSON window rate mean ± sd: {_fmt_rate(result['summary']['transport_valid_json_window_rate']['mean'])} ± {_fmt_rate(result['summary']['transport_valid_json_window_rate']['sd'])}",
    ]
    if result.get("threshold") is not None:
        verdict = "PASS" if result.get("recall_pass") else "FAIL"
        lines.append(f"- recall threshold: {result['threshold']} -> {verdict} (recall-only gate)")
    lines.extend(
        [
            "",
            "## Per-run overall metrics",
            "| run | matched/gold | recall | recall 95% CI | agreement-with-Opus | agreement 95% CI | F1 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in result["runs"]:
        lines.append(_render_metric_row(str(run["run"]), run["code_level"]))
    lines.extend(
        [
            "",
            "## Quote-strict recall",
            "| run | matched/gold | recall | recall 95% CI | agreement-with-Opus | agreement 95% CI | F1 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in result["runs"]:
        lines.append(_render_metric_row(str(run["run"]), run["quote_strict"]))
    lines.extend(
        [
            "",
            "## Baseline reference",
            _render_metric_row("baseline_qwen", result["baseline"]["code_level"]),
            "",
            "## Per-bucket caution",
            "Per-bucket v0 sample sizes are under ~30 findings, so bucket rows are illustrative only.",
        ]
    )
    return "\n".join(lines) + "\n"


def default_result_dir(dataset: BenchmarkDataset, model_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return dataset.root.parent / "results" / f"{_safe_name(model_name)}-{stamp}"
