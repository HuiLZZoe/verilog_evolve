"""Version records, scoring adapters, and promotion-gate helpers."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluators import build_evaluators
from evaluators.heldout_functional import HeldOutFunctionalEvaluator
from scoring import ScoreSummary, score_results


@dataclass
class Evaluation:
    passed: bool
    result: str
    score: float
    mismatch_count: int | None = None
    sample_count: int | None = None
    failure_kind: str = "unknown"
    analysis: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    evaluator_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CandidateRecord:
    task_id: str
    version: str
    parent_version: str | None
    stage: str
    strategy: str
    completion_path: str
    passed: bool
    score: float
    result: str
    failure_kind: str
    mismatch_count: int | None
    sample_count: int | None
    analysis: dict[str, Any]
    metrics: dict[str, Any]
    evaluator_results: list[dict[str, Any]]
    prompt_context: dict[str, Any]
    critical_paths: list[dict[str, Any]] = field(default_factory=list)
    optimization_plan: dict[str, Any] = field(default_factory=dict)
    sec_status: str = "not_run"
    wns_before: float | None = None
    wns_after: float | None = None
    tns_before: float | None = None
    tns_after: float | None = None
    area_before: float | None = None
    area_after: float | None = None
    skill_source: str = "static"
    equivalence_risk: str = "unknown"
    promotion_gate: dict[str, Any] = field(default_factory=dict)


DEFAULT_STRATEGIES = ("direct", "c_bridge", "repair")


def evaluation_from_score(summary: ScoreSummary) -> Evaluation:
    return Evaluation(
        passed=summary.passed,
        result=summary.result,
        score=summary.score,
        mismatch_count=summary.mismatch_count,
        sample_count=summary.sample_count,
        failure_kind=summary.failure_kind,
        analysis=summary.analysis,
        metrics=summary.metrics,
        evaluator_results=summary.evaluator_results,
    )


def evaluation_from_record(record: CandidateRecord) -> Evaluation:
    return Evaluation(
        passed=record.passed,
        result=record.result,
        score=record.score,
        mismatch_count=record.mismatch_count,
        sample_count=record.sample_count,
        failure_kind=record.failure_kind,
        analysis=record.analysis,
        metrics=record.metrics,
        evaluator_results=record.evaluator_results,
    )


def evaluate_candidate(
    problem: dict[str, Any],
    completion: str,
    timeout: float,
    work_dir: Path,
    evaluators: list[Any],
    score_config: dict[str, Any],
) -> Evaluation:
    results = [
        evaluator.evaluate(problem=problem, completion=completion, timeout=timeout, work_dir=work_dir / evaluator.name)
        for evaluator in evaluators
    ]
    return evaluation_from_score(score_results(results, score_config))


def evaluate_promotion_gate(
    problem: dict[str, Any],
    completion: str,
    args: argparse.Namespace,
    work_dir: Path,
) -> dict[str, Any]:
    """Run hidden tests that are used only for major-version promotion."""
    if not getattr(args, "heldout_tests", False):
        return {"enabled": False, "passed": True, "result": "disabled"}
    evaluator = HeldOutFunctionalEvaluator(samples=args.heldout_samples, seed=args.heldout_seed)
    result = evaluator.evaluate(problem=problem, completion=completion, timeout=args.timeout, work_dir=work_dir)
    return {
        "enabled": True,
        "passed": result.passed,
        "result": result.result,
        "metrics": result.metrics,
        "feedback": result.feedback,
        "artifacts": result.artifacts,
    }


def is_ppa_mode(score_config: dict[str, Any], evaluator_names: str) -> bool:
    if score_config.get("mode") == "dr_rtl_sota":
        return True
    names = {item.strip().lower() for item in evaluator_names.split(",") if item.strip()}
    if names & {"eda", "eda_dc_sec", "dc_sec", "sec", "yosys", "abc", "timing"}:
        return True
    return any(float(weight or 0.0) > 0.0 for weight in score_config.get("weights", {}).values())


def metrics_snapshot(metrics: dict[str, Any] | None) -> dict[str, float | None]:
    metrics = metrics or {}
    return {
        "wns": _float_or_none(metrics.get("wns")),
        "tns": _float_or_none(metrics.get("tns")),
        "area": _float_or_none(metrics.get("area")),
    }


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_critical_paths_from_evaluation(evaluation: Evaluation | CandidateRecord | None) -> list[dict[str, Any]]:
    if evaluation is None:
        return []
    metrics = getattr(evaluation, "metrics", {}) or {}
    paths = metrics.get("critical_paths") or getattr(evaluation, "critical_paths", [])
    if not isinstance(paths, list):
        return []
    normalized = [path for path in paths if isinstance(path, dict)]
    return sorted(normalized, key=lambda item: float(item.get("slack") if item.get("slack") is not None else 0.0))


def infer_sec_status(evaluation: Evaluation) -> str:
    if "eda_sec_passed" in evaluation.metrics:
        return "pass" if evaluation.metrics.get("eda_sec_passed") else "fail"
    if "sec_passed" in evaluation.metrics:
        return "pass" if evaluation.metrics.get("sec_passed") else "fail"
    return "not_run"


def infer_equivalence_risk(optimization_plan: dict[str, Any], sec_status: str) -> str:
    if sec_status == "pass":
        return "low"
    focus = str(optimization_plan.get("optimization_focus", ""))
    if focus == "sequential":
        return "high"
    if sec_status == "fail":
        return "high"
    return "medium"


def evaluate_baseline(
    task_id: str,
    problem: dict[str, Any],
    args: argparse.Namespace,
    evaluators: list[Any],
    score_config: dict[str, Any],
    normalize_completion: Any,
) -> dict[str, Any]:
    baseline_completion = ""
    if args.baseline_rtl:
        baseline_path = Path(args.baseline_rtl)
        if baseline_path.exists():
            source = baseline_path.read_text(encoding="utf-8")
            baseline_completion = normalize_completion(source)
    baseline_completion = baseline_completion or str(problem.get("canonical_solution", "")).strip()
    if not baseline_completion:
        return {"available": False, "reason": "no baseline RTL or canonical_solution"}

    baseline_dir = Path(args.out_dir) / task_id / "eval" / "baseline_v0"
    baseline_eval = evaluate_candidate(problem, baseline_completion, args.timeout, baseline_dir, evaluators, score_config)
    snapshot = metrics_snapshot(baseline_eval.metrics)
    if score_config.get("mode") == "dr_rtl_sota" and all(value is not None for value in snapshot.values()):
        score_config["baseline_metrics"] = snapshot
    return {
        "available": True,
        "completion": baseline_completion,
        "evaluation": asdict(baseline_eval),
        "metrics": baseline_eval.metrics,
        "critical_paths": get_critical_paths_from_evaluation(baseline_eval),
    }


def make_candidate_record(
    *,
    task_id: str,
    version: str,
    parent_version: str | None,
    stage: str,
    strategy: str,
    completion_path: str,
    evaluation: Evaluation,
    context: dict[str, Any],
    critical_paths: list[dict[str, Any]],
    optimization_plan: dict[str, Any],
    current_metrics: dict[str, float | None],
) -> CandidateRecord:
    candidate_metrics = metrics_snapshot(evaluation.metrics)
    sec_status = infer_sec_status(evaluation)
    return CandidateRecord(
        task_id=task_id,
        version=version,
        parent_version=parent_version,
        stage=stage,
        strategy=strategy,
        completion_path=completion_path,
        passed=evaluation.passed,
        score=evaluation.score,
        result=evaluation.result,
        failure_kind=evaluation.failure_kind,
        mismatch_count=evaluation.mismatch_count,
        sample_count=evaluation.sample_count,
        analysis=evaluation.analysis,
        metrics=evaluation.metrics,
        evaluator_results=evaluation.evaluator_results,
        prompt_context=context,
        critical_paths=critical_paths,
        optimization_plan=optimization_plan,
        sec_status=sec_status,
        wns_before=current_metrics.get("wns"),
        wns_after=candidate_metrics.get("wns"),
        tns_before=current_metrics.get("tns"),
        tns_after=candidate_metrics.get("tns"),
        area_before=current_metrics.get("area"),
        area_after=candidate_metrics.get("area"),
        skill_source=str(context.get("skill_source", "static")),
        equivalence_risk=infer_equivalence_risk(optimization_plan, sec_status),
    )
