"""History, candidate artifact, and cross-task evidence persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from versioning import CandidateRecord


def save_candidate(
    run_dir: Path,
    task_id: str,
    version: str,
    completion: str,
    problem: dict[str, Any],
) -> Path:
    candidate_dir = run_dir / task_id / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    path = candidate_dir / f"{version}.sv"
    prompt = str(problem.get("prompt", ""))
    path.write_text(f"{prompt}\n{completion}\n", encoding="utf-8")
    return path


def save_major_version(
    run_dir: Path,
    task_id: str,
    version: str,
    completion: str,
    problem: dict[str, Any],
) -> Path:
    major_dir = run_dir / task_id / "major"
    major_dir.mkdir(parents=True, exist_ok=True)
    path = major_dir / f"{version}.sv"
    prompt = str(problem.get("prompt", ""))
    path.write_text(f"{prompt}\n{completion}\n", encoding="utf-8")
    return path


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def group_relative_summary(records: list[CandidateRecord]) -> dict[str, Any]:
    if not records:
        return {}
    best = min(records, key=lambda item: item.score)
    sec_pass = [record.version for record in records if record.sec_status == "pass"]
    return {
        "best_version": best.version,
        "best_strategy": best.strategy,
        "best_score": best.score,
        "sec_pass_versions": sec_pass,
        "strategy_scores": [
            {
                "version": record.version,
                "strategy": record.strategy,
                "path_selection": record.optimization_plan.get("path_selection"),
                "optimization_focus": record.optimization_plan.get("optimization_focus"),
                "score": record.score,
                "sec_status": record.sec_status,
                "wns_after": record.wns_after,
                "tns_after": record.tns_after,
                "area_after": record.area_after,
                "promotion_gate": record.promotion_gate,
            }
            for record in sorted(records, key=lambda item: item.score)
        ],
    }


def write_task_history(
    task_dir: Path,
    *,
    task_id: str,
    best: CandidateRecord,
    current_major_version: str | None,
    end_reason: str,
    major_rounds: list[dict[str, Any]],
) -> None:
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


def write_summary(task_dir: Path, best: CandidateRecord, best_completion: str, major_rounds: list[dict[str, Any]], end_reason: str) -> dict[str, Any]:
    summary = {
        "task_id": best.task_id,
        "best_version": best.version,
        "best_score": best.score,
        "current_major_version": major_rounds[-1].get("promoted_to") if major_rounds else None,
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


def update_skill_evidence(out_dir: Path, skills_dir: Path, summaries: list[dict[str, Any]]) -> None:
    """Persist lightweight cross-task evidence, similar to SkillClaw skill history."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = skills_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    aggregate: dict[str, dict[str, Any]] = {}
    for item in summaries:
        kind = str(item.get("failure_kind") or ("passed" if item.get("passed") else "unknown"))
        bucket = aggregate.setdefault(kind, {"count": 0, "passed": 0, "examples": []})
        bucket["count"] += 1
        if item.get("passed"):
            bucket["passed"] += 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(
                {
                    "task_id": item.get("task_id"),
                    "best_version": item.get("best_version"),
                    "best_score": item.get("best_score"),
                    "analysis": item.get("analysis"),
                }
            )

    evidence_path = evidence_dir / "run_evidence.json"
    evidence_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")

    lessons = [
        "# Auto-Collected Verilog Evolution Evidence",
        "",
        "This file is generated from `result_evolve.py` run summaries. Treat it as evidence for",
        "updating or pruning SKILL.md guidance; do not blindly promote one-off failures.",
        "",
    ]
    for kind, data in sorted(aggregate.items()):
        lessons.append(f"## {kind}")
        lessons.append(f"- Observed: {data['count']} task(s); passed best candidates: {data['passed']}")
        for example in data["examples"]:
            tags = ", ".join((example.get("analysis") or {}).get("tags", []))
            lessons.append(f"- `{example['task_id']}` best `{example['best_version']}` score `{example['best_score']}` tags: {tags}")
        lessons.append("")

    (evidence_dir / "run_evidence.md").write_text("\n".join(lessons), encoding="utf-8")


def record_dict(record: CandidateRecord) -> dict[str, Any]:
    return asdict(record)
