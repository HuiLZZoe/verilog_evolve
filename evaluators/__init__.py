"""Evaluator registry for result-grounded Verilog evolution."""

from __future__ import annotations

from pathlib import Path

from .abc_timing import ABCTimingEvaluator
from .base import BaseEvaluator, EvaluationResult
from .downstream import DownstreamEvaluator
from .eda_dc_sec import EDADCSecEvaluator
from .functional import FunctionalEvaluator
from .heldout_functional import HeldOutFunctionalEvaluator
from .synthesis_yosys import YosysSynthesisEvaluator


def build_evaluators(names: list[str], *, downstream_spec: str = "") -> list[BaseEvaluator]:
    evaluators: list[BaseEvaluator] = []
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        if key == "functional":
            evaluators.append(FunctionalEvaluator())
        elif key in {"heldout", "heldout_functional"}:
            evaluators.append(HeldOutFunctionalEvaluator())
        elif key in {"yosys", "synthesis", "synthesis_yosys"}:
            evaluators.append(YosysSynthesisEvaluator())
        elif key in {"abc", "timing", "abc_timing"}:
            evaluators.append(ABCTimingEvaluator())
        elif key == "downstream":
            evaluators.append(DownstreamEvaluator(Path(downstream_spec) if downstream_spec else None))
        elif key in {"eda", "eda_dc_sec", "dc_sec", "sec"}:
            evaluators.append(EDADCSecEvaluator())
        else:
            raise ValueError(f"Unknown evaluator '{name}'")
    return evaluators


__all__ = [
    "ABCTimingEvaluator",
    "BaseEvaluator",
    "DownstreamEvaluator",
    "EDADCSecEvaluator",
    "EvaluationResult",
    "FunctionalEvaluator",
    "HeldOutFunctionalEvaluator",
    "YosysSynthesisEvaluator",
    "build_evaluators",
]
