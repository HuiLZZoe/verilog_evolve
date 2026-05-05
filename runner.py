"""Main result-grounded Verilog evolution runner."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VERILOG_EVAL_ROOT = ROOT / "verilog-eval"
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(VERILOG_EVAL_ROOT) not in sys.path:
    sys.path.append(str(VERILOG_EVAL_ROOT))

from evaluators import build_evaluators  # noqa: E402
from generation import (  # noqa: E402
    generate_candidate,
    get_description,
    load_descriptions,
    load_skill_guidance,
    normalize_completion,
    repair_candidate,
)
from history import (  # noqa: E402
    append_jsonl,
    group_relative_summary,
    record_dict,
    save_candidate,
    save_major_version,
    update_skill_evidence,
)
from planning import enrich_description_with_plan, make_diversity_plan  # noqa: E402
from scoring import load_score_config  # noqa: E402
from skill_evolver import evolve_skills_from_history  # noqa: E402
from skill_extractor import extract_skill_updates  # noqa: E402
from verilog_eval.data import read_problems, write_jsonl  # noqa: E402
from verilog_eval.execution import clean_up_simulation  # noqa: E402
from versioning import (  # noqa: E402
    DEFAULT_STRATEGIES,
    CandidateRecord,
    evaluate_baseline,
    evaluate_candidate,
    evaluate_promotion_gate,
    evaluation_from_record,
    get_critical_paths_from_evaluation,
    is_ppa_mode,
    make_candidate_record,
    metrics_snapshot,
)


def run_task(
    task_id: str,
    problem: dict[str, Any],
    description_row: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    run_dir = Path(args.out_dir)
    task_dir = run_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    description = get_description(description_row)
    head = str(problem.get("prompt", ""))
    iter_history_path = task_dir / "history.jsonl"
    best: CandidateRecord | None = None
    best_completion = ""
    current_major: CandidateRecord | None = None
    current_major_completion = ""
    current_major_version: str | None = None
    major_rounds: list[dict[str, Any]] = []
    end_reason = "max_rounds"
    skill_guidance = load_skill_guidance(args.skills_dir)
    strategies = tuple(args.strategies.split(",")) if args.strategies else DEFAULT_STRATEGIES
    evaluators = build_evaluators(args.evaluators.split(","), downstream_spec=args.downstream_spec)
    score_config = load_score_config(args.score_config)
    ppa_mode = is_ppa_mode(score_config, args.evaluators)
    baseline = evaluate_baseline(task_id, problem, args, evaluators, score_config, normalize_completion)
    baseline_metrics = metrics_snapshot(baseline.get("metrics") if baseline.get("available") else {})
    current_metrics = dict(baseline_metrics)
    current_critical_paths = list(baseline.get("critical_paths") or [])
    baseline_summary_path = task_dir / "baseline.json"
    baseline_summary_path.write_text(
        json.dumps({k: v for k, v in baseline.items() if k != "completion"}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for major in range(args.rounds):
        parent_version = current_major_version
        round_records: list[CandidateRecord] = []
        completions_by_version: dict[str, str] = {}
        round_best: CandidateRecord | None = None
        round_best_completion = ""

        for minor in range(args.candidates):
            version = f"v{major}.{minor + 1}"
            strategy = strategies[minor % len(strategies)]
            optimization_plan = make_diversity_plan(major, minor, current_critical_paths)
            planned_description = enrich_description_with_plan(description, optimization_plan)
            attempt_skill_guidance = load_skill_guidance(
                args.skills_dir,
                task_description=description,
                timing_paths=optimization_plan.get("selected_paths", []),
                optimization_plan=optimization_plan,
            ) or skill_guidance
            if strategy == "repair" and current_major and current_major_completion:
                completion, context = repair_candidate(
                    planned_description,
                    head,
                    current_major_completion,
                    evaluation_from_record(current_major),
                    attempt_skill_guidance,
                )
                stage = "repair"
            else:
                actual_strategy = "c_bridge" if args.use_c_bridge and strategy == "direct" else strategy
                if actual_strategy == "repair":
                    actual_strategy = "direct"
                completion, context = generate_candidate(planned_description, head, actual_strategy, attempt_skill_guidance)
                strategy = actual_strategy
                stage = "generate"
            context["optimization_plan"] = optimization_plan
            context["skill_source"] = "retrieved" if attempt_skill_guidance != skill_guidance else "static"
            context["task_description"] = description
            context["module_head"] = head

            completion_path = save_candidate(run_dir, task_id, version, completion, problem)
            eval_work_dir = task_dir / "eval" / version
            evaluation = evaluate_candidate(problem, completion, args.timeout, eval_work_dir, evaluators, score_config)
            critical_paths = get_critical_paths_from_evaluation(evaluation) or optimization_plan.get("selected_paths", [])
            record = make_candidate_record(
                task_id=task_id,
                version=version,
                parent_version=parent_version,
                stage=stage,
                strategy=strategy,
                completion_path=str(completion_path),
                evaluation=evaluation,
                context=context,
                critical_paths=critical_paths,
                optimization_plan=optimization_plan,
                current_metrics=current_metrics,
            )
            append_jsonl(iter_history_path, record_dict(record))
            round_records.append(record)
            completions_by_version[version] = completion

            if round_best is None or record.score < round_best.score:
                round_best = record
                round_best_completion = completion

            if best is None or record.score < best.score:
                best = record
                best_completion = completion

            if record.passed and not ppa_mode:
                break

        if round_best is None:
            continue

        if ppa_mode:
            sec_pass_records = [record for record in round_records if record.sec_status in {"pass", "not_run"} and record.passed]
            if sec_pass_records:
                round_best = min(sec_pass_records, key=lambda item: item.score)
                round_best_completion = completions_by_version[round_best.version]

        gate = evaluate_promotion_gate(
            problem,
            round_best_completion,
            args,
            task_dir / "eval" / round_best.version / "promotion_gate",
        )
        round_best.promotion_gate = gate
        improved = current_major is None or round_best.score < current_major.score
        should_promote = (improved or current_major is None) and bool(gate.get("passed", True))
        promoted_to = None
        major_path = None
        if should_promote:
            promoted_to = f"v{major + 1}"
            current_major = round_best
            current_major_completion = round_best_completion
            current_major_version = promoted_to
            current_metrics = metrics_snapshot(current_major.metrics)
            current_critical_paths = current_major.critical_paths
            major_path = save_major_version(run_dir, task_id, promoted_to, current_major_completion, problem)
        elif gate.get("enabled") and not gate.get("passed"):
            end_reason = "heldout_failed"

        major_rounds.append(
            {
                "major_index": major,
                "base_version": parent_version,
                "minors": [asdict(item) for item in round_records],
                "selected_minor": round_best.version,
                "selected_score": round_best.score,
                "selected_passed": round_best.passed,
                "promotion_gate": gate,
                "group_relative": group_relative_summary(round_records),
                "improved": improved,
                "promoted_to": promoted_to,
                "major_path": str(major_path) if major_path else None,
            }
        )

        if round_best.passed and (not gate.get("enabled") or gate.get("passed")):
            end_reason = "passed"
            break
        if args.stop_on_no_improvement and current_major is not None and not improved:
            end_reason = "no_improvement"
            break

    if best is None:
        raise RuntimeError(f"No candidate generated for {task_id}")

    (task_dir / "history.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "best_version": best.version,
                "best_score": best.score,
                "current_major_version": current_major_version,
                "end_reason": end_reason,
                "major_rounds": major_rounds,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = {
        "task_id": task_id,
        "best_version": best.version,
        "best_score": best.score,
        "current_major_version": current_major_version,
        "passed": best.passed,
        "result": best.result,
        "failure_kind": best.failure_kind,
        "analysis": best.analysis,
        "metrics": best.metrics,
        "evaluator_results": best.evaluator_results,
        "completion": best_completion,
        "major_rounds": len(major_rounds),
        "end_reason": end_reason,
    }
    (task_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run result-grounded self-evolution on VerilogEval tasks.")
    parser.add_argument("--problem-file", required=True, help="Path to VerilogEval_*.jsonl")
    parser.add_argument("--description-file", required=True, help="Path to VerilogDescription_*.jsonl")
    parser.add_argument("--task-id", action="append", help="Task id to run. Can be repeated. Defaults to all tasks.")
    parser.add_argument("--out-dir", default=str(ROOT / "runs" / "result_evolve"), help="Output run directory")
    parser.add_argument("--rounds", type=int, default=3, help="Major rounds per task")
    parser.add_argument("--candidates", type=int, default=5, help="Candidates per major round")
    parser.add_argument("--timeout", type=float, default=30.0, help="Simulation timeout per candidate")
    parser.add_argument("--use-c-bridge", action="store_true", help="Prefer C-bridge generation for direct slots")
    parser.add_argument("--strategies", default="direct,c_bridge,repair", help="Comma-separated generation strategies")
    parser.add_argument("--evaluators", default="functional", help="Comma-separated evaluators: functional,yosys,abc,downstream")
    parser.add_argument("--score-config", default="", help="Optional JSON scoring config")
    parser.add_argument("--downstream-spec", default="", help="Optional downstream task spec JSON")
    parser.add_argument("--baseline-rtl", default="", help="Optional baseline RTL file for v0 EDA/PPA normalization")
    parser.add_argument("--skills-dir", default=str(ROOT / "skills"), help="Directory containing SkillClaw-style skills")
    parser.add_argument("--heldout-tests", action="store_true", help="Require generated held-out tests to pass before major promotion")
    parser.add_argument("--heldout-samples", type=int, default=64, help="Randomized held-out samples for supported GEMM tasks")
    parser.add_argument("--heldout-seed", type=int, default=0, help="Seed for generated held-out tests")
    parser.add_argument("--update-skill-evidence", action="store_true", help="Write cross-task evidence under skills/evidence")
    parser.add_argument("--extract-skills", action="store_true", help="Extract evidence-backed guidance into skills/auto-extracted/SKILL.md after the run")
    parser.add_argument("--evolve-skills", action="store_true", help="Write SkillClaw-style create/improve/skip decisions from history.json")
    parser.add_argument("--skill-publish-mode", choices=("immediate", "validated"), default="immediate", help="Skill publish mode for evolver")
    parser.add_argument("--skill-validation-required-results", type=int, default=2, help="Validation mode: number of validation votes")
    parser.add_argument("--skill-validation-required-approvals", type=int, default=2, help="Validation mode: approvals required for publish")
    parser.add_argument("--skill-validation-min-mean-score", type=float, default=0.75, help="Validation mode: mean score threshold")
    parser.add_argument("--skill-min-support", type=int, default=2, help="Minimum repeated evidence count for auto-extracted skill guidance")
    parser.add_argument("--stop-on-no-improvement", action="store_true", help="Stop a task when a full major round does not improve the current major baseline")
    parser.add_argument("--clean-up", action="store_true", help="Kill hanging iverilog/vvp processes after the run")
    args = parser.parse_args()

    strategies = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    invalid = sorted(set(strategies) - set(DEFAULT_STRATEGIES))
    if invalid:
        raise ValueError(f"Unsupported strategies: {invalid}. Supported: {DEFAULT_STRATEGIES}")
    args.strategies = ",".join(strategies)
    evaluator_names = [item.strip() for item in args.evaluators.split(",") if item.strip()]
    if "functional" not in evaluator_names:
        evaluator_names.insert(0, "functional")
    if args.score_config and "dr_rtl_sota" in Path(args.score_config).name and "eda_dc_sec" not in evaluator_names:
        evaluator_names.append("eda_dc_sec")
    args.evaluators = ",".join(evaluator_names)

    problems = read_problems(args.problem_file)
    descriptions = load_descriptions(args.description_file)
    run_context = {
        "problem_file": args.problem_file,
        "description_file": args.description_file,
        "timeout": args.timeout,
        "evaluators": args.evaluators,
        "score_config": args.score_config,
    }
    task_ids = args.task_id or list(problems.keys())

    summaries = []
    for task_id in task_ids:
        if task_id not in problems:
            raise KeyError(f"Task {task_id!r} not found in {args.problem_file}")
        if task_id not in descriptions:
            raise KeyError(f"Task {task_id!r} not found in {args.description_file}")
        summaries.append(run_task(task_id, problems[task_id], descriptions[task_id], args))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_context.json").write_text(json.dumps(run_context, indent=2, ensure_ascii=False), encoding="utf-8")
    sample_path = out_dir / "best_samples.jsonl"
    write_jsonl(str(sample_path), ({"task_id": item["task_id"], "completion": item["completion"]} for item in summaries))
    (out_dir / "run_summary.json").write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.update_skill_evidence:
        update_skill_evidence(out_dir, Path(args.skills_dir), summaries)
    if args.extract_skills:
        extraction = extract_skill_updates(out_dir, Path(args.skills_dir), min_support=args.skill_min_support)
        (out_dir / "skill_extraction.json").write_text(json.dumps(extraction, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.evolve_skills:
        evolve_skills_from_history(
            out_dir,
            Path(args.skills_dir),
            min_sec_pass=args.skill_min_support,
            publish_mode=args.skill_publish_mode,
            validation_required_results=args.skill_validation_required_results,
            validation_required_approvals=args.skill_validation_required_approvals,
            validation_min_mean_score=args.skill_validation_min_mean_score,
        )

    if args.clean_up:
        clean_up_simulation()

    passed = sum(1 for item in summaries if item["passed"])
    print(f"Completed {len(summaries)} task(s). Passed: {passed}. Samples: {sample_path}")


if __name__ == "__main__":
    main()
