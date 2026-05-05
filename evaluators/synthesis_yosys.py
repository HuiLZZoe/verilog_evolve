"""Yosys synthesis evaluator for downstream-friendly RTL metrics."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import EvaluationResult, extract_top_module, module_source


def _collect_stat_metrics(stat: dict[str, Any]) -> dict[str, Any]:
    modules = stat.get("modules") if isinstance(stat, dict) else None
    if not isinstance(modules, dict) or not modules:
        return {}
    module_data = next(iter(modules.values()))
    cells = module_data.get("cells", {}) if isinstance(module_data, dict) else {}
    wires = module_data.get("wires", {}) if isinstance(module_data, dict) else {}
    cell_count = sum(int(v) for v in cells.values()) if isinstance(cells, dict) else 0
    return {
        "cell_count": cell_count,
        "wire_count": int(wires.get("num_wires", 0)) if isinstance(wires, dict) else 0,
        "wire_bits": int(wires.get("num_wire_bits", 0)) if isinstance(wires, dict) else 0,
        "num_cells_by_type": cells if isinstance(cells, dict) else {},
    }


class YosysSynthesisEvaluator:
    name = "yosys"

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
                feedback=["Install yosys or run without the yosys evaluator."],
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        source = module_source(problem, completion)
        top = extract_top_module(source)
        source_path = work_dir / "synthesis_input.sv"
        source_path.write_text(source, encoding="utf-8")
        top_cmd = f"hierarchy -check -top {top}; " if top else "hierarchy -check -auto-top; "
        cmd = [
            "yosys",
            "-q",
            "-p",
            f"read_verilog -sv {source_path}; {top_cmd}proc; opt; techmap; opt; stat -json",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(timeout, 10.0))
        except subprocess.TimeoutExpired:
            return EvaluationResult(name=self.name, passed=False, result="yosys timed out", feedback=["Yosys synthesis timed out."])

        if proc.returncode != 0:
            return EvaluationResult(
                name=self.name,
                passed=False,
                result=(proc.stderr or proc.stdout).strip(),
                feedback=["Yosys failed to synthesize the design; simplify unsupported constructs or fix synthesis errors."],
            )

        try:
            stat = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return EvaluationResult(
                name=self.name,
                passed=False,
                result="failed to parse yosys stat json",
                feedback=["Yosys completed but did not emit parseable stat JSON."],
            )

        metrics = _collect_stat_metrics(stat)
        feedback = [
            f"Yosys cell_count={metrics.get('cell_count', 0)}, wire_count={metrics.get('wire_count', 0)}, wire_bits={metrics.get('wire_bits', 0)}."
        ]
        return EvaluationResult(name=self.name, passed=True, result="passed", metrics=metrics, feedback=feedback)
