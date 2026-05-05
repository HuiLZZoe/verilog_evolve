"""Evidence-backed skill extractor for Verilog evolution histories."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _iter_history_records(run_dir: Path):
    for history_path in run_dir.glob("*/history.jsonl"):
        with history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def extract_skill_updates(run_dir: Path, skills_dir: Path, *, min_support: int = 2) -> dict[str, Any]:
    tag_counter: Counter[str] = Counter()
    hint_counter: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    for record in _iter_history_records(run_dir):
        analysis = record.get("analysis") if isinstance(record, dict) else {}
        if not isinstance(analysis, dict):
            continue
        tags = [str(tag) for tag in analysis.get("tags", []) if str(tag)]
        hints = [str(hint) for hint in analysis.get("repair_hints", []) if str(hint)]
        task_id = str(record.get("task_id", ""))
        version = str(record.get("version", ""))
        for tag in tags:
            tag_counter[tag] += 1
            if len(examples[tag]) < 5:
                examples[tag].append(f"{task_id}:{version}")
        for hint in hints:
            hint_counter[hint] += 1

    promoted_tags = {tag: count for tag, count in tag_counter.items() if count >= min_support and tag != "passed"}
    promoted_hints = {hint: count for hint, count in hint_counter.items() if count >= min_support}

    auto_dir = skills_dir / "auto-extracted"
    auto_dir.mkdir(parents=True, exist_ok=True)
    skill_path = auto_dir / "SKILL.md"
    evidence_path = auto_dir / "evidence.json"

    evidence = {
        "min_support": min_support,
        "promoted_tags": promoted_tags,
        "promoted_hints": promoted_hints,
        "examples": {tag: examples[tag] for tag in promoted_tags},
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "---",
        "name: auto-extracted",
        "description: Evidence-backed Verilog repair guidance extracted from repeated result_evolve histories. NOT for: one-off failures below support threshold.",
        "category: verilog",
        "---",
        "",
        "## Verified Failure Patterns",
        "",
    ]
    if not promoted_tags:
        lines.append("- No repeated failure pattern reached the support threshold yet.")
    for tag, count in sorted(promoted_tags.items(), key=lambda item: (-item[1], item[0])):
        sample_text = ", ".join(examples[tag])
        lines.append(f"- `{tag}` appeared {count} times. Evidence: {sample_text}.")

    lines.extend(["", "## Promoted Repair Hints", ""])
    if not promoted_hints:
        lines.append("- No repair hint reached the support threshold yet.")
    for hint, count in sorted(promoted_hints.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- ({count}x) {hint}")

    lines.extend(
        [
            "",
            "## Verifier Rule",
            "",
            f"Only use guidance from this skill when the same pattern appears in at least {min_support} logged attempts.",
            "Treat this skill as evidence-backed but still secondary to simulator, synthesis, and timing feedback.",
            "",
        ]
    )
    skill_path.write_text("\n".join(lines), encoding="utf-8")
    return {"skill_path": str(skill_path), "evidence_path": str(evidence_path), **evidence}
