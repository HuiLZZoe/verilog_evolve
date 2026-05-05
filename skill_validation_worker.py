#!/usr/bin/env python3
"""External validation worker for queued skill-evolution jobs.

This worker is intentionally independent from the evolver process:
- polls queued jobs in ``skills/.evolver_store/validation_jobs.jsonl``
- replays a subset of cases with baseline vs candidate skill guidance
- writes validator results back to the job record
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VERILOG_EVAL_ROOT = ROOT / "verilog-eval"
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(VERILOG_EVAL_ROOT) not in sys.path:
    sys.path.append(str(VERILOG_EVAL_ROOT))

from evaluators import build_evaluators  # noqa: E402
from generation import generate_candidate  # noqa: E402
from scoring import load_score_config  # noqa: E402
from versioning import evaluate_candidate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run external replay validation for queued skill jobs.")
    parser.add_argument("--skills-dir", default=str(ROOT / "skills"), help="Skills root containing .evolver_store")
    parser.add_argument("--max-jobs", type=int, default=1, help="Maximum pending jobs to process")
    parser.add_argument("--max-cases", type=int, default=3, help="Maximum replay cases per job")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-case evaluator timeout")
    parser.add_argument("--validator-id", default="", help="Optional validator id")
    args = parser.parse_args()

    validator_id = args.validator_id.strip() or f"{socket.gethostname()}-{os.getpid()}"
    store = Path(args.skills_dir) / ".evolver_store"
    jobs_path = store / "validation_jobs.jsonl"
    if not jobs_path.exists():
        print("No validation queue found.")
        return

    jobs = _read_jobs(jobs_path)
    processed = 0
    updated = False
    for job in jobs:
        if processed >= args.max_jobs:
            break
        if str(job.get("status", "")).lower() != "pending":
            continue
        if _has_validator_result(job, validator_id):
            continue
        result = _run_job(job, validator_id=validator_id, max_cases=args.max_cases, timeout=args.timeout, store=store)
        job.setdefault("results", []).append(result)
        processed += 1
        updated = True

    if updated:
        _write_jobs(jobs_path, jobs)
    print(f"Validation worker processed {processed} job(s) as {validator_id}.")


def _run_job(job: dict[str, Any], *, validator_id: str, max_cases: int, timeout: float, store: Path) -> dict[str, Any]:
    cases = [case for case in (job.get("replay_cases") or []) if isinstance(case, dict)]
    if not cases:
        return _result(validator_id, approved=False, score=0.0, reason="job has no replay_cases", details={})

    candidate_skill = str(job.get("candidate_skill_text", "") or "")
    baseline_skill = str(job.get("current_skill_text", "") or "")
    per_case = []
    for idx, case in enumerate(cases[:max_cases]):
        case_result = _run_case(
            case,
            baseline_skill=baseline_skill,
            candidate_skill=candidate_skill,
            timeout=timeout,
            work_dir=store / "worker_runs" / str(job.get("job_id", "unknown")) / f"case_{idx+1}",
        )
        per_case.append(case_result)

    candidate_scores = [float(case["candidate_quality"]) for case in per_case if isinstance(case.get("candidate_quality"), (int, float))]
    baseline_scores = [float(case["baseline_quality"]) for case in per_case if isinstance(case.get("baseline_quality"), (int, float))]
    candidate_mean = sum(candidate_scores) / len(candidate_scores) if candidate_scores else 0.0
    baseline_mean = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0.0
    approved = candidate_mean >= baseline_mean and candidate_mean >= float((job.get("requirements") or {}).get("min_mean_score", 0.75))
    reason = f"candidate_mean={candidate_mean:.3f}, baseline_mean={baseline_mean:.3f}"
    return _result(
        validator_id,
        approved=approved,
        score=round(candidate_mean, 6),
        reason=reason,
        details={"per_case": per_case, "candidate_mean": candidate_mean, "baseline_mean": baseline_mean},
    )


def _run_case(
    case: dict[str, Any],
    *,
    baseline_skill: str,
    candidate_skill: str,
    timeout: float,
    work_dir: Path,
) -> dict[str, Any]:
    instruction = str(case.get("instruction", "")).strip()
    head = str(case.get("module_head", "")).strip()
    problem = case.get("problem") if isinstance(case.get("problem"), dict) else {}
    evaluators = build_evaluators(["functional"])
    score_config = load_score_config("")
    if not instruction or not head or not problem:
        return {"error": "invalid replay case", "candidate_quality": 0.0, "baseline_quality": 0.0}

    baseline_completion, _ = generate_candidate(instruction, head, "direct", baseline_skill)
    baseline_eval = evaluate_candidate(problem, baseline_completion, timeout, work_dir / "baseline", evaluators, score_config)
    candidate_completion, _ = generate_candidate(instruction, head, "direct", candidate_skill)
    candidate_eval = evaluate_candidate(problem, candidate_completion, timeout, work_dir / "candidate", evaluators, score_config)

    baseline_quality = _quality_from_eval(baseline_eval)
    candidate_quality = _quality_from_eval(candidate_eval)
    return {
        "task_id": case.get("task_id"),
        "baseline_quality": baseline_quality,
        "candidate_quality": candidate_quality,
        "baseline_passed": baseline_eval.passed,
        "candidate_passed": candidate_eval.passed,
        "baseline_score": baseline_eval.score,
        "candidate_score": candidate_eval.score,
    }


def _quality_from_eval(evaluation: Any) -> float:
    if evaluation.passed:
        return 1.0
    score = float(evaluation.score) if isinstance(evaluation.score, (int, float)) else 3.0
    return max(0.0, min(1.0, 1.0 - (score / 3.0)))


def _result(validator_id: str, *, approved: bool, score: float, reason: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "validator": validator_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved": approved,
        "decision": "accept" if approved else "reject",
        "score": score,
        "reason": reason,
        "details": details,
    }


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            jobs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return jobs


def _write_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(job, ensure_ascii=False) for job in jobs) + "\n", encoding="utf-8")


def _has_validator_result(job: dict[str, Any], validator_id: str) -> bool:
    for item in job.get("results", []):
        if isinstance(item, dict) and str(item.get("validator", "")) == validator_id:
            return True
    return False


if __name__ == "__main__":
    main()
