"""Task and timing-pattern aware skill retrieval."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SkillCandidate:
    path: Path
    name: str
    text: str
    score: float
    reasons: list[str]


def retrieve_skill_guidance(
    skills_dir: str | Path,
    *,
    task_description: str = "",
    timing_paths: list[dict[str, Any]] | None = None,
    optimization_plan: dict[str, Any] | None = None,
    top_k: int = 4,
    max_chars: int = 6000,
) -> str:
    root = Path(skills_dir)
    if not root.exists():
        return ""
    query_terms = _terms(task_description)
    path_terms = _path_terms(timing_paths or [])
    plan_terms = _terms(" ".join(str(value) for value in (optimization_plan or {}).values()))
    candidates: list[SkillCandidate] = []
    registry = _load_registry(root / ".evolver_store" / "evolve_skill_registry.json")
    for skill_path in sorted(root.glob("*/SKILL.md")):
        try:
            text = skill_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        lower = text.lower()
        score = 0.0
        reasons: list[str] = []
        for term in query_terms:
            if term in lower:
                score += 1.0
        for term in path_terms:
            if term in lower:
                score += 2.0
                reasons.append(f"path:{term}")
        for term in plan_terms:
            if term in lower:
                score += 1.5
                reasons.append(f"plan:{term}")
        if skill_path.parent.name in {"verilog-generation", "simulator-feedback"}:
            score += 0.5
        if skill_path.parent.name in {"rtl-opt", "timing-feedback", "synthesis-feedback"} and (path_terms or plan_terms):
            score += 2.0
            reasons.append("timing_or_synthesis_context")
        score += _registry_bonus(registry, skill_path.parent.name)
        candidates.append(SkillCandidate(skill_path, skill_path.parent.name, text, score, reasons))

    selected = sorted(candidates, key=lambda item: (-item.score, item.name))[:top_k]
    chunks: list[str] = []
    for item in selected:
        reason_text = ", ".join(item.reasons) if item.reasons else "default"
        chunks.append(f"# {item.name}\nRetrieval reasons: {reason_text}\n{item.text}")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    return "\n\n".join(chunks)[:max_chars]


def _terms(text: str) -> set[str]:
    tokens = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)}
    stop = {"module", "input", "output", "wire", "logic", "always", "begin", "end", "verilog"}
    return tokens - stop


def _path_terms(paths: list[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()
    for path in paths[:10]:
        for key in ("path", "startpoint", "endpoint", "startpoint_word", "endpoint_word"):
            terms.update(_terms(str(path.get(key, ""))))
    structural = {"mux", "fanout", "state", "counter", "adder", "compare", "decode", "fsm", "pipeline", "register"}
    return terms | {term for term in structural if any(term in str(path).lower() for path in paths)}


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _registry_bonus(registry: dict[str, Any], skill_name: str) -> float:
    skills = registry.get("skills", {}) if isinstance(registry, dict) else {}
    for key, value in skills.items():
        if skill_name in str(key):
            versions = value.get("versions", []) if isinstance(value, dict) else []
            return min(len(versions) * 0.2, 1.5)
    return 0.0
