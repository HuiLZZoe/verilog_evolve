"""Configurable multi-objective scoring for Verilog evolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluators import EvaluationResult


DEFAULT_SCORE_CONFIG: dict[str, Any] = {
    "mode": "default",
    "required_evaluators": ["functional"],
    "hard_gates": [],
    "penalties": {
        "mismatch_base": 1.0,
        "compile_error": 2.3,
        "syntax_error": 2.6,
        "timeout": 3.0,
        "unknown_failure": 2.0,
        "optional_evaluator_fail": 0.2,
        "sec_failure": 1000.0,
        "missing_baseline": 10.0,
    },
    "weights": {
        "cell_count": 0.0,
        "wire_count": 0.0,
        "wire_bits": 0.0,
        "abc_delay_proxy": 0.0,
        "downstream_score": 0.0,
    },
    "normalizers": {
        "cell_count": 1000.0,
        "wire_count": 1000.0,
        "wire_bits": 10000.0,
        "abc_delay_proxy": 10.0,
        "downstream_score": 10.0,
    },
}


@dataclass
class ScoreSummary:
    passed: bool
    score: float
    result: str
    failure_kind: str
    mismatch_count: int | None = None
    sample_count: int | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    evaluator_results: list[dict[str, Any]] = field(default_factory=list)


def load_score_config(path: str = "") -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_SCORE_CONFIG))
    if not path:
        return config
    user_config = json.loads(Path(path).read_text(encoding="utf-8"))
    for key, value in user_config.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def _functional_score(functional: EvaluationResult | None, config: dict[str, Any]) -> tuple[float, str, int | None, int | None, dict[str, Any]]:
    penalties = config.get("penalties", {})
    if functional is None:
        return penalties.get("unknown_failure", 2.0), "missing_functional", None, None, {
            "failure_kind": "missing_functional",
            "tags": ["missing_functional"],
            "repair_hints": ["Run the functional evaluator before trusting downstream metrics."],
        }
    if functional.passed:
        return 0.0, "passed", None, None, {"failure_kind": "passed", "tags": ["passed"], "repair_hints": []}

    failure_kind = str(functional.metrics.get("failure_kind", "unknown_failure"))
    mismatch_count = functional.metrics.get("mismatch_count")
    sample_count = functional.metrics.get("sample_count")
    if failure_kind == "mismatch" and isinstance(mismatch_count, int) and isinstance(sample_count, int):
        ratio = mismatch_count / max(sample_count, 1)
        score = float(penalties.get("mismatch_base", 1.0)) + ratio
    else:
        score = float(penalties.get(failure_kind, penalties.get("unknown_failure", 2.0)))
    analysis = {
        "failure_kind": failure_kind,
        "tags": [failure_kind],
        "repair_hints": list(functional.feedback),
    }
    return score, failure_kind, mismatch_count, sample_count, analysis


def _normalized_delta(value: Any, baseline: Any, *, absolute_baseline: bool = False) -> float | None:
    if isinstance(value, bool) or isinstance(baseline, bool) or value is None or baseline is None:
        return None
    try:
        numerator = float(value) - float(baseline)
        denominator = abs(float(baseline)) if absolute_baseline else float(baseline)
    except (TypeError, ValueError):
        return None
    if denominator == 0:
        denominator = 1.0
    return numerator / denominator


def _score_dr_rtl(results: list[EvaluationResult], config: dict[str, Any]) -> ScoreSummary:
    by_name = {result.name: result for result in results}
    eda = by_name.get("eda_dc_sec")
    functional = by_name.get("functional")
    metrics: dict[str, Any] = {}
    feedback: list[str] = []
    for result in results:
        metrics.update(result.metrics)
        feedback.extend(result.feedback)

    required = set(config.get("required_evaluators", ["functional", "eda_dc_sec"]))
    passed = all(by_name.get(name) and by_name[name].passed for name in required)
    sec_passed = bool(metrics.get("eda_sec_passed") or metrics.get("sec_passed"))
    if "eda_dc_sec" in required and not sec_passed:
        passed = False

    baseline = config.get("baseline_metrics", {}) if isinstance(config.get("baseline_metrics"), dict) else {}
    wns_norm = _normalized_delta(metrics.get("wns"), baseline.get("wns"), absolute_baseline=True)
    tns_norm = _normalized_delta(metrics.get("tns"), baseline.get("tns"), absolute_baseline=True)
    area_norm = _normalized_delta(metrics.get("area"), baseline.get("area"))
    weights = config.get("dr_rtl_weights", {"wns": 0.5, "tns": 0.35, "area": 0.15})
    penalties = config.get("penalties", {})

    missing_norms = [name for name, value in {"wns_norm": wns_norm, "tns_norm": tns_norm, "area_norm": area_norm}.items() if value is None]
    if missing_norms:
        score = float(penalties.get("missing_baseline", 10.0))
    else:
        score = (
            float(weights.get("wns", 0.5)) * float(wns_norm)
            + float(weights.get("tns", 0.35)) * float(tns_norm)
            + float(weights.get("area", 0.15)) * float(area_norm)
        )
    area_penalty_threshold = float(config.get("area_penalty_threshold", 0.10))
    area_penalty = float(config.get("area_penalty", 0.5)) if area_norm is not None and area_norm > area_penalty_threshold else 0.0
    score += area_penalty
    if not sec_passed:
        score += float(penalties.get("sec_failure", 1000.0))

    failure_kind = "passed" if passed else ("sec_failure" if not sec_passed else "eda_failure")
    if functional is not None and not functional.passed:
        functional_score, failure_kind, mismatch_count, sample_count, functional_analysis = _functional_score(functional, config)
        score += functional_score
    else:
        mismatch_count = None
        sample_count = None
        functional_analysis = {"tags": ["passed"] if passed else [failure_kind], "repair_hints": []}

    analysis = {
        **functional_analysis,
        "failure_kind": failure_kind,
        "repair_hints": feedback,
        "evaluator_status": {result.name: result.passed for result in results},
        "dr_rtl_scoring": {
            "baseline": baseline,
            "wns_norm": wns_norm,
            "tns_norm": tns_norm,
            "area_norm": area_norm,
            "area_penalty": area_penalty,
            "sec_passed": sec_passed,
            "missing_norms": missing_norms,
        },
    }
    result_text = eda.result if eda else "missing eda_dc_sec evaluator"
    return ScoreSummary(
        passed=passed,
        score=round(score, 6),
        result=result_text,
        failure_kind=failure_kind,
        mismatch_count=mismatch_count,
        sample_count=sample_count,
        analysis=analysis,
        metrics=metrics,
        evaluator_results=[
            {
                "name": result.name,
                "passed": result.passed,
                "result": result.result,
                "metrics": result.metrics,
                "feedback": result.feedback,
                "artifacts": result.artifacts,
            }
            for result in results
        ],
    )


def score_results(results: list[EvaluationResult], config: dict[str, Any]) -> ScoreSummary:
    if config.get("mode") == "dr_rtl_sota":
        return _score_dr_rtl(results, config)

    by_name = {result.name: result for result in results}
    functional = by_name.get("functional")
    score, failure_kind, mismatch_count, sample_count, analysis = _functional_score(functional, config)
    required = set(config.get("required_evaluators", ["functional"]))
    passed = all(by_name.get(name) and by_name[name].passed for name in required)
    metrics: dict[str, Any] = {}
    feedback: list[str] = list(analysis.get("repair_hints", []))

    for result in results:
        metrics.update(result.metrics)
        if result.name != "functional":
            feedback.extend(result.feedback)
        if result.name in required and not result.passed:
            passed = False
        elif result.name not in required and not result.passed:
            score += float(config.get("penalties", {}).get("optional_evaluator_fail", 0.2))

    for gate in config.get("hard_gates", []):
        if gate == "sec_passed" and not bool(metrics.get("eda_sec_passed") or metrics.get("sec_passed")):
            passed = False

    for metric_name, weight in config.get("weights", {}).items():
        value = metrics.get(metric_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        normalizer = float(config.get("normalizers", {}).get(metric_name, 1.0)) or 1.0
        score += float(weight) * (float(value) / normalizer)

    analysis = {
        **analysis,
        "repair_hints": feedback,
        "evaluator_status": {result.name: result.passed for result in results},
    }
    result_text = functional.result if functional else "missing functional evaluator"
    return ScoreSummary(
        passed=passed,
        score=round(score, 6),
        result=result_text,
        failure_kind=failure_kind,
        mismatch_count=mismatch_count,
        sample_count=sample_count,
        analysis=analysis,
        metrics=metrics,
        evaluator_results=[
            {
                "name": result.name,
                "passed": result.passed,
                "result": result.result,
                "metrics": result.metrics,
                "feedback": result.feedback,
                "artifacts": result.artifacts,
            }
            for result in results
        ],
    )
