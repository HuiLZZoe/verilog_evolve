"""Diversity planning for major/minor RTL evolution."""

from __future__ import annotations

import random
from typing import Any


def make_diversity_plan(major: int, minor: int, critical_paths: list[dict[str, Any]]) -> dict[str, Any]:
    path_modes = ("top_k_worst_slack", "endpoint_clustering", "high_fanout", "module_focused", "random_sample")
    focus_modes = ("combinational", "sequential", "mixed")
    path_mode = path_modes[minor % len(path_modes)]
    focus = focus_modes[(major + minor) % len(focus_modes)]
    selected_paths = select_paths_for_plan(path_mode, critical_paths)
    return {
        "minor_index": minor + 1,
        "path_selection": path_mode,
        "optimization_focus": focus,
        "selected_paths": selected_paths,
        "instructions": plan_instructions(path_mode, focus, selected_paths),
    }


def select_paths_for_plan(path_mode: str, critical_paths: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not critical_paths:
        return []
    if path_mode == "random_sample":
        sample = critical_paths[:]
        random.shuffle(sample)
        return sample[:limit]
    if path_mode == "endpoint_clustering":
        seen: set[str] = set()
        selected: list[dict[str, Any]] = []
        for path in critical_paths:
            endpoint = str(path.get("endpoint_word") or path.get("endpoint") or path.get("path", ""))
            if endpoint in seen:
                continue
            seen.add(endpoint)
            selected.append(path)
            if len(selected) >= limit:
                break
        return selected
    if path_mode == "high_fanout":
        counts: dict[str, int] = {}
        for path in critical_paths:
            for key in ("startpoint_word", "endpoint_word"):
                name = str(path.get(key, ""))
                if name:
                    counts[name] = counts.get(name, 0) + 1
        return sorted(
            critical_paths,
            key=lambda path: max(
                counts.get(str(path.get("startpoint_word", "")), 0),
                counts.get(str(path.get("endpoint_word", "")), 0),
            ),
            reverse=True,
        )[:limit]
    if path_mode == "module_focused":
        buckets: dict[str, list[dict[str, Any]]] = {}
        for path in critical_paths:
            raw = str(path.get("path") or path.get("endpoint_word") or path.get("endpoint") or "")
            module = raw.split("/", 1)[0].split(".", 1)[0]
            buckets.setdefault(module, []).append(path)
        largest = max(buckets.values(), key=len)
        return largest[:limit]
    return critical_paths[:limit]


def plan_instructions(path_mode: str, focus: str, selected_paths: list[dict[str, Any]]) -> str:
    paths = "\n".join(
        f"- {path.get('path') or (str(path.get('startpoint_word', '')) + ' -> ' + str(path.get('endpoint_word', '')))} slack={path.get('slack')}"
        for path in selected_paths
    )
    return (
        f"Timing optimization plan: use {path_mode} path selection with {focus} focus. "
        "Preserve module interface, reset behavior, and pipeline latency unless formal equivalence is expected to prove it. "
        f"Target paths:\n{paths or '- No timing paths available; use conservative synthesis-friendly RTL.'}"
    )


def enrich_description_with_plan(description: str, optimization_plan: dict[str, Any]) -> str:
    instructions = str(optimization_plan.get("instructions", "")).strip()
    if not instructions:
        return description
    return f"{description}\n\n{instructions}"
