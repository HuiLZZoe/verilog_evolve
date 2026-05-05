"""Downstream task evaluator for hardware co-design objectives.

The evaluator keeps the original text-level checks as a fallback, but prefers a
Yosys JSON netlist when Yosys is available. Netlist-level cell counts are much
harder to game than counting ``*`` in the source and give downstream GEMM/PE
experiments a more credible hardware signal.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import EvaluationResult, extract_top_module, module_source


NETLIST_CELL_ALIASES = {
    "mul_cells": ("$mul", "$macc", "$__mul", "$__soft_mul"),
    "add_cells": ("$add", "$sub", "$alu", "$__add", "$__sub"),
    "dff_cells": ("$dff", "$adff", "$sdff", "$dffe", "$adffe", "$sdffe", "$_dff"),
    "mux_cells": ("$mux", "$pmux", "$tribuf", "$_mux"),
}


def _cell_kind(cell_type: str) -> str | None:
    lowered = cell_type.lower()
    for metric, aliases in NETLIST_CELL_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            return metric
    return None


def _parse_yosys_netlist(netlist: dict[str, Any]) -> dict[str, Any]:
    modules = netlist.get("modules") if isinstance(netlist, dict) else None
    if not isinstance(modules, dict) or not modules:
        return {}

    top_name = ""
    for name, module in modules.items():
        attributes = module.get("attributes", {}) if isinstance(module, dict) else {}
        if str(attributes.get("top", "")).strip() in {"1", "true"}:
            top_name = str(name)
            break
    module_data = modules.get(top_name) if top_name else next(iter(modules.values()))
    cells = module_data.get("cells", {}) if isinstance(module_data, dict) else {}
    if not isinstance(cells, dict):
        cells = {}

    metrics = {
        "mul_cells": 0,
        "add_cells": 0,
        "dff_cells": 0,
        "mux_cells": 0,
        "netlist_cell_count": len(cells),
    }
    for cell in cells.values():
        if not isinstance(cell, dict):
            continue
        kind = _cell_kind(str(cell.get("type", "")))
        if kind:
            metrics[kind] += 1

    mul_cells = metrics["mul_cells"]
    add_cells = metrics["add_cells"]
    dff_cells = metrics["dff_cells"]
    mux_cells = metrics["mux_cells"]
    metrics["estimated_mac_lanes"] = min(mul_cells, add_cells) if add_cells else mul_cells
    metrics["pipeline_depth_proxy"] = 1 if dff_cells > 0 else 0
    if metrics["estimated_mac_lanes"]:
        metrics["pipeline_depth_proxy"] = max(1, round(dff_cells / max(metrics["estimated_mac_lanes"], 1), 3))
    area_proxy = mul_cells * 6.0 + add_cells * 2.0 + mux_cells * 0.5 + dff_cells * 0.8
    delay_proxy = max(1.0, mul_cells * 2.0 + add_cells * 0.7 + mux_cells * 0.2 - metrics["pipeline_depth_proxy"] * 0.3)
    metrics["area_proxy"] = round(area_proxy, 3)
    metrics["delay_proxy"] = round(delay_proxy, 3)
    metrics["area_delay_product_proxy"] = round(area_proxy * delay_proxy, 3)
    return metrics


def _run_yosys_json(problem: dict[str, Any], completion: str, timeout: float, work_dir: Path) -> tuple[dict[str, Any], str]:
    if shutil.which("yosys") is None:
        return {}, "yosys not found; falling back to source-level downstream heuristics"

    work_dir.mkdir(parents=True, exist_ok=True)
    source = module_source(problem, completion)
    top = extract_top_module(source)
    source_path = work_dir / "downstream_input.sv"
    json_path = work_dir / "downstream_netlist.json"
    source_path.write_text(source, encoding="utf-8")
    top_cmd = f"hierarchy -check -top {top}; " if top else "hierarchy -check -auto-top; "
    cmd = [
        "yosys",
        "-q",
        "-p",
        f"read_verilog -sv {source_path}; {top_cmd}proc; opt; memory; opt; techmap; opt; write_json {json_path}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(timeout, 10.0))
    except subprocess.TimeoutExpired:
        return {}, "yosys downstream netlist extraction timed out"
    if proc.returncode != 0:
        return {}, (proc.stderr or proc.stdout or "yosys downstream netlist extraction failed").strip()
    try:
        return json.loads(json_path.read_text(encoding="utf-8")), f"parsed yosys json netlist: {json_path}"
    except (OSError, json.JSONDecodeError):
        return {}, "failed to parse yosys downstream netlist JSON"


class DownstreamEvaluator:
    name = "downstream"

    def __init__(self, spec_path: Path | None = None) -> None:
        self.spec_path = spec_path
        self.spec = self._load_spec(spec_path)

    @staticmethod
    def _load_spec(spec_path: Path | None) -> dict[str, Any]:
        if not spec_path:
            return {}
        try:
            return json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        if not self.spec:
            return EvaluationResult(
                name=self.name,
                passed=True,
                result="skipped: no downstream spec",
                metrics={"downstream_score": 0.0},
                feedback=["No downstream spec provided; downstream evaluator is neutral."],
            )

        text = completion.lower()
        constraints = self.spec.get("constraints", {})
        preferred = constraints.get("preferred_bitwidths", [])
        bitwidth_hits = 0
        for bitwidth in preferred:
            if re.search(rf"\[\s*{int(bitwidth) - 1}\s*:\s*0\s*\]", text):
                bitwidth_hits += 1

        netlist, netlist_status = _run_yosys_json(problem, completion, timeout, work_dir)
        netlist_metrics = _parse_yosys_netlist(netlist)
        multiplier_count = int(netlist_metrics.get("mul_cells", 0)) or len(re.findall(r"\*", completion))
        pipeline_hint = len(re.findall(r"always\s*@\s*\(\s*posedge", text))
        target_multiplier_budget = int(constraints.get("max_multipliers", 999999))
        multiplier_penalty = max(0, multiplier_count - target_multiplier_budget)
        bitwidth_score = bitwidth_hits / max(len(preferred), 1) if preferred else 1.0
        mac_target = int(constraints.get("target_mac_lanes", 0) or 0)
        mac_lanes = int(netlist_metrics.get("estimated_mac_lanes", 0) or 0)
        mac_lane_penalty = max(0, mac_target - mac_lanes) * 0.25 if mac_target else 0.0
        adp = float(netlist_metrics.get("area_delay_product_proxy", 0.0) or 0.0)
        adp_penalty = adp / float(constraints.get("area_delay_normalizer", 100.0) or 100.0)
        downstream_score = round(
            multiplier_penalty
            + (1.0 - bitwidth_score)
            + max(0, 1 - pipeline_hint) * 0.2
            + mac_lane_penalty
            + adp_penalty,
            3,
        )

        metrics = {
            "downstream_score": downstream_score,
            "preferred_bitwidth_hits": bitwidth_hits,
            "multiplier_count": multiplier_count,
            "pipeline_register_blocks": pipeline_hint,
            "task_id": self.spec.get("task_id", ""),
            **netlist_metrics,
        }
        feedback = [
            f"Downstream score={downstream_score}; multipliers={multiplier_count}, preferred_bitwidth_hits={bitwidth_hits}, pipeline_blocks={pipeline_hint}.",
            netlist_status,
        ]
        if netlist_metrics:
            feedback.append(
                "Netlist metrics: "
                f"mul={metrics.get('mul_cells', 0)}, add={metrics.get('add_cells', 0)}, "
                f"dff={metrics.get('dff_cells', 0)}, mux={metrics.get('mux_cells', 0)}, "
                f"mac_lanes={metrics.get('estimated_mac_lanes', 0)}, "
                f"pipeline_depth={metrics.get('pipeline_depth_proxy', 0)}, "
                f"ADP_proxy={metrics.get('area_delay_product_proxy', 0)}."
            )
        if multiplier_penalty:
            feedback.append("Multiplier count exceeds downstream task budget; consider sharing or staged MAC structure.")
        if preferred and bitwidth_hits == 0:
            feedback.append("No preferred quantized bitwidth pattern was detected.")
        return EvaluationResult(
            name=self.name,
            passed=True,
            result="passed",
            metrics=metrics,
            feedback=feedback,
            artifacts={"netlist_json": str(work_dir / "downstream_netlist.json")} if netlist_metrics else {},
        )
