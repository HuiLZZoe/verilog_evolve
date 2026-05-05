"""DC/Jasper SEC evaluator wrapper.

This evaluator exposes Dr. RTL-style industrial PPA feedback to the generic
result_evolve loop. Tool execution is delegated to ``eda_flow.dc_sec_adapter``
so users can plug in local scripts, remote runners, or parse-only artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from eda_flow import EDAFlowAdapter, EDAFlowConfig

from .base import EvaluationResult, extract_top_module, module_source


class EDADCSecEvaluator:
    name = "eda_dc_sec"

    def __init__(
        self,
        *,
        flow_root: str = "",
        flow_command: str = "",
        golden_rtl: str = "",
        design_name: str = "",
        parse_only: bool = False,
    ) -> None:
        self.flow_root = flow_root or os.environ.get("VERILOG_EVOLVE_EDA_FLOW_ROOT", "")
        self.flow_command = flow_command or os.environ.get("VERILOG_EVOLVE_EDA_FLOW_CMD", "")
        self.golden_rtl = golden_rtl or os.environ.get("VERILOG_EVOLVE_GOLDEN_RTL", "")
        self.design_name = design_name or os.environ.get("VERILOG_EVOLVE_DESIGN", "")
        self.parse_only = parse_only or os.environ.get("VERILOG_EVOLVE_EDA_PARSE_ONLY", "").lower() in {"1", "true", "yes"}

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        source = module_source(problem, completion)
        top = extract_top_module(source)
        design_name = self.design_name or str(problem.get("task_id") or top or "candidate")
        version = work_dir.parent.name if work_dir.parent.name else "candidate"
        config = EDAFlowConfig(
            design_name=design_name,
            design_top=top,
            golden_rtl=self.golden_rtl,
            flow_root=self.flow_root,
            flow_command=self.flow_command,
            parse_only=self.parse_only,
            clock=str(problem.get("clock", "")),
            reset=str(problem.get("reset", "")),
        )
        result = EDAFlowAdapter(config).evaluate(source=source, version=version, timeout=timeout, work_dir=work_dir)
        return EvaluationResult(
            name=self.name,
            passed=result.passed,
            result=result.result,
            metrics=result.metrics,
            feedback=result.feedback,
            artifacts=result.artifacts,
        )
