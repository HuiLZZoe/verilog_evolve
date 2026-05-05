"""Embedded SkillClaw-style workflow for cross-session skill evolution.

Pipeline:
1. Ingest run artifacts as session payloads.
2. Drain pending sessions from shared local store.
3. Summarize sessions and extract metadata.
4. Aggregate sessions by referenced skills (multi-membership).
5. Evolve existing skills or create new skills per group.
6. Verify, then publish immediately or through validation queue.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill_verifier import verify_skill_candidate

NO_SKILL_KEY = "__no_skill__"
REGISTRY_NAME = "evolve_skill_registry.json"


@dataclass
class EvolveConfig:
    min_sec_pass: int = 2
    publish_mode: str = "immediate"
    validation_required_results: int = 2
    validation_required_approvals: int = 2
    validation_min_mean_score: float = 0.75


def evolve_skills_from_history(
    run_dir: Path,
    skills_dir: Path,
    *,
    min_sec_pass: int = 2,
    publish_mode: str = "immediate",
    validation_required_results: int = 2,
    validation_required_approvals: int = 2,
    validation_min_mean_score: float = 0.75,
) -> dict[str, Any]:
    config = EvolveConfig(
        min_sec_pass=min_sec_pass,
        publish_mode=publish_mode,
        validation_required_results=validation_required_results,
        validation_required_approvals=validation_required_approvals,
        validation_min_mean_score=validation_min_mean_score,
    )
    store = _store_dir(skills_dir)
    sessions_dir = store / "sessions" / "pending"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _ingest_run_sessions(run_dir, sessions_dir)
    drained_sessions = _drain_sessions(sessions_dir)
    summaries = [_summarize_session(session) for session in drained_sessions]
    grouped = _aggregate_sessions_by_skill(summaries)

    artifacts_dir = run_dir / "skill_evolution"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    registry = _load_registry(store)
    decisions: list[dict[str, Any]] = []
    for skill_key, sessions in sorted(grouped.items()):
        records = _flatten_records(sessions)
        if not records:
            continue
        decision = _evolve_group(skill_key, sessions, records, skills_dir, artifacts_dir, config, registry)
        decisions.append(decision)

    validation_summary = _finalize_validation_jobs(skills_dir, store, config, registry)
    _save_registry(store, registry)
    output = {
        "mode": config.publish_mode,
        "drained_sessions": len(drained_sessions),
        "group_count": len(grouped),
        "decisions": decisions,
        "validation_summary": validation_summary,
        "artifacts_dir": str(artifacts_dir),
    }
    out_path = run_dir / "skill_evolution_decisions.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


def _store_dir(skills_dir: Path) -> Path:
    return skills_dir / ".evolver_store"


def _ingest_run_sessions(run_dir: Path, sessions_dir: Path) -> None:
    run_context = _read_json(run_dir / "run_context.json")
    problem_map = _load_problem_map(str(run_context.get("problem_file", "")))
    for history_path in run_dir.glob("*/history.json"):
        task_id = history_path.parent.name
        session_id = f"{task_id}-{hashlib.sha256(str(history_path).encode('utf-8')).hexdigest()[:8]}"
        session_file = sessions_dir / f"{session_id}.json"
        if session_file.exists():
            continue
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session = {
            "session_id": session_id,
            "task_id": task_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_history": str(history_path),
            "run_context": run_context,
            "major_rounds": history.get("major_rounds", []),
            "summary": _read_json(history_path.parent / "summary.json"),
            "problem": problem_map.get(task_id, {}),
        }
        session_file.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


def _drain_sessions(sessions_dir: Path) -> list[dict[str, Any]]:
    drained: list[dict[str, Any]] = []
    for session_file in sorted(sessions_dir.glob("*.json")):
        try:
            drained.append(json.loads(session_file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        session_file.unlink(missing_ok=True)
    return drained


def _summarize_session(session: dict[str, Any]) -> dict[str, Any]:
    rounds = session.get("major_rounds", [])
    records = [record for major_round in rounds for record in major_round.get("minors", [])]
    references: set[str] = set()
    for record in records:
        plan = record.get("optimization_plan") or {}
        for token in (
            plan.get("path_selection"),
            plan.get("optimization_focus"),
            record.get("strategy"),
            record.get("skill_source"),
        ):
            if token:
                references.add(str(token))
    metadata = {
        "_skills_referenced": sorted(references),
        "_records": records,
        "_sec_pass_count": sum(1 for record in records if record.get("sec_status") == "pass" or record.get("passed")),
        "_avg_score": _mean([record.get("score") for record in records]),
    }
    return {**session, **metadata}


def _aggregate_sessions_by_skill(summaries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session in summaries:
        references = session.get("_skills_referenced") or []
        if not references:
            grouped[NO_SKILL_KEY].append(session)
            continue
        for skill in references:
            grouped[str(skill)].append(session)
    return grouped


def _flatten_records(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for session in sessions:
        for record in session.get("_records", []):
            enriched = dict(record)
            enriched["_session_id"] = session.get("session_id")
            records.append(enriched)
    return records


def _evolve_group(
    skill_key: str,
    sessions: list[dict[str, Any]],
    records: list[dict[str, Any]],
    skills_dir: Path,
    artifacts_dir: Path,
    config: EvolveConfig,
    registry: dict[str, Any],
) -> dict[str, Any]:
    existing_path = _matching_skill_path(skills_dir, skill_key)
    candidate = _build_candidate(skill_key, records)
    verification = verify_skill_candidate(candidate, min_sec_pass=config.min_sec_pass)
    if not verification["accepted"]:
        action = "skip"
    elif existing_path:
        action = "improve_skill"
    else:
        action = "create_skill"
    artifacts = _write_skill_artifacts(
        artifacts_dir,
        skills_dir,
        key=skill_key,
        action=action,
        candidate=candidate,
        verification=verification,
        records=records,
    )
    publish = _publish_or_queue(
        skills_dir=skills_dir,
        skill_key=skill_key,
        action=action,
        candidate=candidate,
        verification=verification,
        artifact_path=artifacts["candidate_skill_path"],
        config=config,
        registry=registry,
        sessions=sessions,
    )
    return {
        "skill_key": skill_key,
        "action": action,
        "candidate": candidate,
        "verification": verification,
        "publish": publish,
        "session_count": len(sessions),
        "records_count": len(records),
        "artifacts": artifacts,
    }


def _build_candidate(skill_key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    promoted = [record for record in records if bool((record.get("promotion_gate") or {}).get("passed", True))]
    sec_pass_records = [record for record in records if record.get("sec_status") == "pass" or record.get("passed")]
    score_deltas = [
        float(record.get("score")) - float(record.get("wns_before") or 0.0)
        for record in records
        if isinstance(record.get("score"), (int, float))
    ]
    evidence = _evidence_summary(records, sec_pass_records, promoted, score_deltas)
    strategy = _dominant_strategy(sec_pass_records or records)
    pattern = "novel-pattern" if skill_key == NO_SKILL_KEY else skill_key
    return {
        "name": f"rtl-opt-{_slug(pattern)}",
        "pattern": pattern,
        "strategy": strategy,
        "equivalence_risk": _aggregate_equivalence_risk(records),
        "evidence": evidence,
    }


def _publish_or_queue(
    *,
    skills_dir: Path,
    skill_key: str,
    action: str,
    candidate: dict[str, Any],
    verification: dict[str, Any],
    artifact_path: str,
    config: EvolveConfig,
    registry: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not verification.get("accepted"):
        return {"status": "rejected", "reason": verification.get("reasons", [])}
    if config.publish_mode == "validated":
        job = _queue_validation_job(skills_dir, candidate, skill_key, artifact_path, sessions, config)
        return {"status": "queued", "job_id": job["job_id"]}
    skill_path = _publish_candidate_skill(skills_dir, candidate, artifact_path)
    _update_registry(registry, candidate, skill_path)
    return {"status": "published", "path": str(skill_path)}


def _queue_validation_job(
    skills_dir: Path,
    candidate: dict[str, Any],
    skill_key: str,
    artifact_path: str,
    sessions: list[dict[str, Any]],
    config: EvolveConfig,
) -> dict[str, Any]:
    jobs_path = _store_dir(skills_dir) / "validation_jobs.jsonl"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    current_skill_path = _matching_skill_path(skills_dir, skill_key)
    current_skill_text = current_skill_path.read_text(encoding="utf-8") if current_skill_path and current_skill_path.exists() else ""
    job = {
        "job_id": str(uuid.uuid4()),
        "skill_name": candidate["name"],
        "skill_key": skill_key,
        "candidate_skill_path": artifact_path,
        "candidate_skill_text": Path(artifact_path).read_text(encoding="utf-8") if Path(artifact_path).exists() else "",
        "current_skill_path": str(current_skill_path) if current_skill_path else "",
        "current_skill_text": current_skill_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "sessions": [session.get("session_id") for session in sessions[:8]],
        "replay_cases": _build_replay_cases(sessions),
        "requirements": {
            "required_results": config.validation_required_results,
            "required_approvals": config.validation_required_approvals,
            "min_mean_score": config.validation_min_mean_score,
        },
        "results": [],
    }
    with jobs_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(job, ensure_ascii=False) + "\n")
    return job


def _finalize_validation_jobs(skills_dir: Path, store: Path, config: EvolveConfig, registry: dict[str, Any]) -> dict[str, Any]:
    jobs_path = store / "validation_jobs.jsonl"
    if not jobs_path.exists():
        return {"processed": 0, "published": 0, "rejected": 0}
    jobs = []
    for line in jobs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            jobs.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    published = 0
    rejected = 0
    updated_jobs = []
    pending = 0
    for job in jobs:
        if job.get("status") != "pending":
            updated_jobs.append(job)
            continue
        gate = _validation_gate_from_results(job)
        if gate["status"] == "pending":
            pending += 1
            updated_jobs.append(job)
            continue
        if gate["approved"]:
            candidate = {"name": job.get("skill_name", "unknown")}
            skill_path = _publish_candidate_skill(
                skills_dir,
                candidate,
                job.get("candidate_skill_path", ""),
            )
            _update_registry(registry, candidate, skill_path)
            job["status"] = "published"
            job["published_path"] = str(skill_path)
            job["publish_summary"] = gate
            published += 1
        else:
            job["status"] = "rejected"
            job["rejection_reason"] = gate["reason"]
            job["publish_summary"] = gate
            rejected += 1
        updated_jobs.append(job)

    jobs_path.write_text("\n".join(json.dumps(job, ensure_ascii=False) for job in updated_jobs) + "\n", encoding="utf-8")
    return {"processed": len(jobs), "pending": pending, "published": published, "rejected": rejected}


def _validation_gate_from_results(job: dict[str, Any]) -> dict[str, Any]:
    requirements = job.get("requirements", {}) if isinstance(job.get("requirements"), dict) else {}
    required_results = max(1, int(requirements.get("required_results", 1) or 1))
    required_approvals = max(1, int(requirements.get("required_approvals", 1) or 1))
    min_mean_score = float(requirements.get("min_mean_score", 0.75) or 0.75)
    results = [item for item in (job.get("results") or []) if isinstance(item, dict)]
    if len(results) < required_results:
        return {
            "status": "pending",
            "approved": False,
            "reason": f"waiting for validator results: {len(results)}/{required_results}",
            "required_results": required_results,
            "required_approvals": required_approvals,
            "min_mean_score": min_mean_score,
        }

    approvals = 0
    scores: list[float] = []
    for item in results:
        approved = item.get("approved")
        if approved is None:
            approved = str(item.get("decision", "")).lower() in {"accept", "approved", "pass", "true"}
        if bool(approved):
            approvals += 1
        score = item.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(float(score))
    mean_score = sum(scores) / len(scores) if scores else 0.0
    ok = approvals >= required_approvals and mean_score >= min_mean_score
    return {
        "status": "ready",
        "approved": ok,
        "reason": "validated" if ok else f"approvals={approvals}/{required_approvals}, mean_score={mean_score:.3f}/{min_mean_score}",
        "approvals": approvals,
        "result_count": len(results),
        "mean_score": round(mean_score, 6),
        "required_results": required_results,
        "required_approvals": required_approvals,
        "min_mean_score": min_mean_score,
    }


def _build_replay_cases(sessions: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for session in sessions:
        task_id = str(session.get("task_id", ""))
        problem = session.get("problem") if isinstance(session.get("problem"), dict) else {}
        records = session.get("_records", []) if isinstance(session.get("_records"), list) else []
        for record in records:
            prompt_context = record.get("prompt_context") if isinstance(record.get("prompt_context"), dict) else {}
            description = str(prompt_context.get("task_description", "")).strip()
            module_head = str(prompt_context.get("module_head", "")).strip() or str(problem.get("prompt", "")).strip()
            if not description or not module_head or not problem:
                continue
            cases.append(
                {
                    "session_id": session.get("session_id"),
                    "task_id": task_id,
                    "instruction": description,
                    "module_head": module_head,
                    "problem": problem,
                    "failure_summary": prompt_context.get("feedback", {}),
                    "strategy": record.get("strategy"),
                    "optimization_plan": record.get("optimization_plan", {}),
                }
            )
            if len(cases) >= limit:
                return cases
    return cases


def _update_registry(registry: dict[str, Any], candidate: dict[str, Any], skill_path: Path) -> None:
    skills = registry.setdefault("skills", {})
    key = candidate.get("name", skill_path.parent.name)
    entry = skills.setdefault(
        key,
        {
            "skill_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
            "versions": [],
        },
    )
    text = skill_path.read_text(encoding="utf-8")
    version = {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "path": str(skill_path),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    entry["versions"].append(version)
    registry["updated_at"] = version["updated_at"]


def _load_registry(store: Path) -> dict[str, Any]:
    path = store / REGISTRY_NAME
    if not path.exists():
        return {"skills": {}, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"skills": {}, "updated_at": None}


def _save_registry(store: Path, registry: dict[str, Any]) -> None:
    path = store / REGISTRY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def _evidence_summary(
    records: list[dict[str, Any]],
    sec_pass_records: list[dict[str, Any]],
    promoted_records: list[dict[str, Any]],
    score_deltas: list[float],
) -> dict[str, Any]:
    def avg_delta(field_after: str, field_before: str) -> float | None:
        deltas = []
        for record in records:
            after = record.get(field_after)
            before = record.get(field_before)
            if isinstance(after, (int, float)) and isinstance(before, (int, float)):
                deltas.append(float(after) - float(before))
        return sum(deltas) / len(deltas) if deltas else None

    return {
        "attempts": len(records),
        "sec_pass": len(sec_pass_records),
        "promoted": len(promoted_records),
        "avg_score_delta": sum(score_deltas) / len(score_deltas) if score_deltas else None,
        "avg_wns_delta": avg_delta("wns_after", "wns_before"),
        "avg_tns_delta": avg_delta("tns_after", "tns_before"),
        "avg_area_delta": avg_delta("area_after", "area_before"),
        "examples": [
            {
                "task_id": record.get("task_id"),
                "version": record.get("version"),
                "score": record.get("score"),
                "sec_status": record.get("sec_status"),
                "promotion_gate": record.get("promotion_gate"),
                "session_id": record.get("_session_id"),
            }
            for record in records[:12]
        ],
    }


def _write_skill_artifacts(
    artifacts_dir: Path,
    skills_dir: Path,
    *,
    key: str,
    action: str,
    candidate: dict[str, Any],
    verification: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, str]:
    safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in key)
    out_dir = artifacts_dir / safe_key
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_path = _matching_skill_path(skills_dir, key)
    old_text = existing_path.read_text(encoding="utf-8") if existing_path and existing_path.exists() else ""
    candidate_text = _render_candidate_skill(candidate, verification, action)

    old_path = out_dir / "old_skill_snapshot.md"
    candidate_path = out_dir / "candidate_skill.md"
    diff_path = out_dir / "candidate_skill.diff"
    evidence_path = out_dir / "evidence.json"
    verifier_path = out_dir / "verifier_report.json"
    old_path.write_text(old_text or "# No Existing Skill\n", encoding="utf-8")
    candidate_path.write_text(candidate_text, encoding="utf-8")
    diff = difflib.unified_diff(
        (old_text or "").splitlines(keepends=True),
        candidate_text.splitlines(keepends=True),
        fromfile="old_skill_snapshot.md",
        tofile="candidate_skill.md",
    )
    diff_path.write_text("".join(diff), encoding="utf-8")
    evidence_path.write_text(json.dumps({"candidate": candidate, "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    verifier_path.write_text(json.dumps({"action": action, "verification": verification}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "old_skill_snapshot_path": str(old_path),
        "candidate_skill_path": str(candidate_path),
        "candidate_skill_diff_path": str(diff_path),
        "evidence_path": str(evidence_path),
        "verifier_report_path": str(verifier_path),
    }


def _render_candidate_skill(candidate: dict[str, Any], verification: dict[str, Any], action: str) -> str:
    evidence = candidate.get("evidence", {})
    lines = [
        "---",
        f"name: {candidate['name']}",
        f"description: Evidence-backed RTL optimization skill for `{candidate['pattern']}` attempts. Action: {action}.",
        "category: verilog",
        "---",
        "",
        "## Use Conditions",
        "",
        f"- Use when the current diversity plan or timing-path selection matches `{candidate['pattern']}`.",
        f"- Dominant successful strategy: `{candidate['strategy']}`.",
        "- Preserve module interface, reset behavior, and latency unless SEC or held-out tests explicitly validate the change.",
        "",
        "## Evidence",
        "",
        f"- Attempts: {evidence.get('attempts', 0)}",
        f"- SEC/functional pass evidence: {evidence.get('sec_pass', 0)}",
        f"- Promoted candidates: {evidence.get('promoted', 0)}",
        f"- Average score delta: {evidence.get('avg_score_delta')}",
        f"- Average WNS/TNS/area deltas: {evidence.get('avg_wns_delta')}, {evidence.get('avg_tns_delta')}, {evidence.get('avg_area_delta')}",
        "",
        "## Guidance",
        "",
        "- Prefer transformations that are supported by repeated pass/promotion evidence in this run.",
        "- If timing paths are available, focus only on the selected startpoint/endpoint cluster.",
        "- Reject changes that fail SEC, visible tests, or held-out promotion tests.",
        "",
        "## Verifier Report",
        "",
        f"- Accepted: {verification.get('accepted')}",
        f"- Reasons: {'; '.join(verification.get('reasons', []))}",
        "",
    ]
    return "\n".join(lines)


def _publish_candidate_skill(skills_dir: Path, candidate: dict[str, Any], candidate_skill_path: str) -> Path:
    target_dir = skills_dir / str(candidate["name"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "SKILL.md"
    source = Path(candidate_skill_path)
    if source.exists():
        target_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target_path


def _dominant_strategy(records: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        strategy = str(record.get("strategy") or "unknown")
        counts[strategy] = counts.get(strategy, 0) + 1
    return max(counts, key=counts.get) if counts else "unknown"


def _matching_skill_path(skills_dir: Path, key: str) -> Path | None:
    lowered = key.lower()
    for skill_path in skills_dir.glob("*/SKILL.md"):
        if lowered in skill_path.parent.name.lower():
            return skill_path
    return None


def _aggregate_equivalence_risk(records: list[dict[str, Any]]) -> str:
    risks = [str(record.get("equivalence_risk", "unknown")) for record in records]
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    if "low" in risks:
        return "low"
    return "unknown"


def _slug(text: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in text]
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "pattern"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_problem_map(problem_file: str) -> dict[str, dict[str, Any]]:
    if not problem_file:
        return {}
    path = Path(problem_file)
    if not path.exists():
        return {}
    problems: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        task_id = str(row.get("task_id", "")).strip()
        if task_id:
            problems[task_id] = row
    return problems


def _mean(values: list[Any]) -> float | None:
    items = [float(item) for item in values if isinstance(item, (int, float))]
    if not items:
        return None
    return sum(items) / len(items)
