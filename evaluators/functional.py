"""Functional evaluator backed by VerilogEval's iverilog/vvp harness."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from verilog_eval.execution import check_correctness

from .base import EvaluationResult


def analyze_failure(result: str, failure_kind: str) -> dict[str, Any]:
    lowered = str(result or "").lower()
    hints: list[str] = []
    tags: list[str] = []
    patterns = [
        ("not a valid l-value", "port_lvalue", "Do not assign directly to a wire output inside procedural blocks; use output reg or an internal reg plus continuous assign."),
        ("incomprehensible for loop", "for_loop", "Declare loop variables at module scope and keep loop bounds static for Icarus Verilog."),
        ("variable declaration in unnamed block", "block_declaration", "Move declarations out of unnamed procedural blocks."),
        ("invalid module instantiation", "invalid_instantiation", "Avoid unsupported SystemVerilog constructs and do not instantiate modules inside always blocks."),
        ("syntax error", "syntax", "Return one complete Verilog module with balanced begin/end and endmodule."),
        ("timed out", "timeout", "Check for combinational loops, missing state progress, or non-terminating simulation behavior."),
        ("mismatches", "functional_mismatch", "Debug reset polarity, sequential update timing, signedness, default assignments, and edge sensitivity."),
        ("info string not matched", "testbench_output", "Check module name, ports, and fatal runtime behavior."),
    ]
    for needle, tag, hint in patterns:
        if needle in lowered:
            tags.append(tag)
            hints.append(hint)
    if not hints:
        hints.append("Preserve the required module declaration and re-check behavior against the task description.")
    return {"failure_kind": failure_kind, "tags": tags or [failure_kind], "repair_hints": hints}


def parse_functional_result(result: str, passed: bool) -> dict[str, Any]:
    if passed:
        return {
            "failure_kind": "passed",
            "mismatch_count": None,
            "sample_count": None,
            "analysis": {"failure_kind": "passed", "tags": ["passed"], "repair_hints": []},
        }

    clean = str(result or "").strip()
    mismatch = re.search(r"failed:\s*(\d+)\s+out of\s+(\d+)\s+samples", clean)
    if mismatch:
        kind = "mismatch"
        return {
            "failure_kind": kind,
            "mismatch_count": int(mismatch.group(1)),
            "sample_count": int(mismatch.group(2)),
            "analysis": analyze_failure(clean, kind),
        }

    lowered = clean.lower()
    if "timed out" in lowered:
        kind = "timeout"
    elif "syntax error" in lowered:
        kind = "syntax_error"
    elif "compile" in lowered or "error" in lowered:
        kind = "compile_error"
    else:
        kind = "unknown_failure"

    return {
        "failure_kind": kind,
        "mismatch_count": None,
        "sample_count": None,
        "analysis": analyze_failure(clean, kind),
    }


class FunctionalEvaluator:
    name = "functional"

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        raw = check_correctness(problem, completion, timeout=timeout, completion_id=0)
        passed = bool(raw.get("passed"))
        result = str(raw.get("result", ""))
        parsed = parse_functional_result(result, passed)
        metrics = {
            "failure_kind": parsed["failure_kind"],
            "mismatch_count": parsed["mismatch_count"],
            "sample_count": parsed["sample_count"],
        }
        return EvaluationResult(
            name=self.name,
            passed=passed,
            result=result,
            metrics=metrics,
            feedback=parsed["analysis"]["repair_hints"],
            artifacts={"completion_id": str(raw.get("completion_id", ""))},
        )
