"""Common evaluator types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class EvaluationResult:
    name: str
    passed: bool
    result: str
    metrics: dict[str, Any] = field(default_factory=dict)
    feedback: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


class BaseEvaluator(Protocol):
    name: str

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        ...


def module_source(problem: dict[str, Any], completion: str) -> str:
    prompt_key = "prompt_pure" if "// myhdl" in completion and "prompt_pure" in problem else "prompt"
    return f"{problem.get(prompt_key, '')}\n{completion}\n"


def extract_top_module(verilog_source: str) -> str:
    import re

    match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:#|\()", verilog_source)
    return match.group(1) if match else ""
