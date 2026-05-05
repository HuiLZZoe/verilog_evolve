"""ABC-based timing proxy evaluator.

This evaluator uses Yosys' ABC integration as a portable proxy when a full STA
environment is unavailable. It reports mapped cell statistics and a simple delay
proxy that can be weighted by the scorer.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import EvaluationResult, extract_top_module, module_source


class ABCTimingEvaluator:
    name = "abc"

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        if shutil.which("yosys") is None:
            return EvaluationResult(
                name=self.name,
                passed=False,
                result="yosys not found on PATH",
                feedback=["ABC timing proxy uses yosys' abc pass; install yosys or disable the abc evaluator."],
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        source = module_source(problem, completion)
        top = extract_top_module(source)
        source_path = work_dir / "abc_input.sv"
        source_path.write_text(source, encoding="utf-8")
        top_cmd = f"hierarchy -check -top {top}; " if top else "hierarchy -check -auto-top; "
        yosys_script = (
            f"read_verilog -sv {source_path}; {top_cmd}"
            "proc; opt; techmap; opt; abc -g AND,OR,XOR,MUX,DFF; opt; stat -json"
        )
        try:
            proc = subprocess.run(["yosys", "-q", "-p", yosys_script], capture_output=True, text=True, timeout=max(timeout, 10.0))
        except subprocess.TimeoutExpired:
            return EvaluationResult(name=self.name, passed=False, result="abc timing proxy timed out", feedback=["ABC mapping timed out."])

        if proc.returncode != 0:
            return EvaluationResult(
                name=self.name,
                passed=False,
                result=(proc.stderr or proc.stdout).strip(),
                feedback=["ABC mapping failed; avoid unsupported constructs and simplify the RTL."],
            )

        try:
            stat = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return EvaluationResult(
                name=self.name,
                passed=False,
                result="failed to parse abc stat json",
                feedback=["ABC completed but Yosys did not emit parseable stat JSON."],
            )

        modules = stat.get("modules", {}) if isinstance(stat, dict) else {}
        module_data = next(iter(modules.values())) if modules else {}
        cells = module_data.get("cells", {}) if isinstance(module_data, dict) else {}
        logic_cells = sum(int(v) for k, v in cells.items() if "$dff" not in str(k).lower()) if isinstance(cells, dict) else 0
        dff_cells = sum(int(v) for k, v in cells.items() if "$dff" in str(k).lower()) if isinstance(cells, dict) else 0
        delay_proxy = round(math.log2(max(logic_cells, 1)) + 0.2 * dff_cells, 3)
        metrics = {
            "abc_logic_cells": logic_cells,
            "abc_dff_cells": dff_cells,
            "abc_delay_proxy": delay_proxy,
            "num_cells_by_type": cells if isinstance(cells, dict) else {},
        }
        feedback = [
            f"ABC timing proxy={delay_proxy}, logic_cells={logic_cells}, dff_cells={dff_cells}. Lower proxy is better."
        ]
        return EvaluationResult(name=self.name, passed=True, result="passed", metrics=metrics, feedback=feedback)
