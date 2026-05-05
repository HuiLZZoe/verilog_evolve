"""Adapter for DC/Jasper-style RTL PPA and SEC evaluation.

The adapter intentionally keeps the tool invocation behind environment/config
switches so the rest of the project can run on machines without commercial EDA
licenses. It can either call an external flow command or parse pre-existing
Dr. RTL-style artifacts under ``output/<design>.<version>/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EDAFlowConfig:
    design_name: str = ""
    design_top: str = ""
    golden_rtl: str = ""
    flow_root: str = ""
    flow_command: str = ""
    parse_only: bool = False
    clock: str = ""
    reset: str = ""
    file_ext: str = "sv"


@dataclass
class EDAFlowResult:
    passed: bool
    sec_passed: bool
    result: str
    metrics: dict[str, Any] = field(default_factory=dict)
    feedback: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


def bit_to_word(reg_name: str) -> str:
    reg_name = re.sub(r"_reg\[\d+\]\[\d+\]$", "", reg_name)
    reg_name = re.sub(r"_reg\[\d+\]$", "", reg_name)
    reg_name = re.sub(r"_reg$", "", reg_name)
    reg_name = re.sub(r"\[\d+\]$", "", reg_name)
    return reg_name


def parse_timing_report(timing_rpt_path: Path) -> list[dict[str, Any]]:
    """Parse a DC timing report into word-level critical paths."""
    if not timing_rpt_path.exists():
        return []

    starts: list[str] = []
    ends: list[str] = []
    slacks: list[float] = []
    for line in timing_rpt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "Startpoint:" in line:
            starts.append(line.split("Startpoint:", 1)[1].split()[0])
        elif "Endpoint:" in line:
            ends.append(line.split("Endpoint:", 1)[1].split()[0])
        else:
            match = re.search(r"slack \(.*\)\s+(\S+)", line)
            if match:
                try:
                    slacks.append(float(match.group(1)))
                except ValueError:
                    continue

    paths_by_key: dict[str, dict[str, Any]] = {}
    for start, end, slack in zip(starts, ends, slacks, strict=False):
        key = f"{bit_to_word(start)} -> {bit_to_word(end)}"
        existing = paths_by_key.get(key)
        if existing is None or slack < float(existing["slack"]):
            paths_by_key[key] = {
                "startpoint": start,
                "endpoint": end,
                "startpoint_word": bit_to_word(start),
                "endpoint_word": bit_to_word(end),
                "slack": slack,
            }
    return sorted(paths_by_key.values(), key=lambda item: float(item["slack"]))


def parse_ppa_report(report_dir: Path) -> dict[str, float | None]:
    """Parse Dr. RTL/DC-style QoR and power reports."""
    area: float | None = None
    wns: float | None = None
    tns: float | None = None
    power: float | None = None

    qor_path = report_dir / "qor.rpt"
    if qor_path.exists():
        for line in qor_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Design Area:" in line:
                area = _parse_float_after_colon(line)
            elif "Critical Path Slack:" in line and wns is None:
                wns = _parse_float_after_colon(line)
            elif "Total Negative Slack:" in line and tns is None:
                tns = _parse_float_after_colon(line)

    power_path = report_dir / "power.rpt"
    if power_path.exists():
        for line in power_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Total Dynamic Power" in line and "=" in line:
                try:
                    power = float(line.split("=", 1)[1].strip().split()[0])
                except (IndexError, ValueError):
                    continue

    return {"area": area, "wns": wns, "tns": tns, "power": power}


def _parse_float_after_colon(line: str) -> float | None:
    try:
        return float(line.split(":", 1)[1].strip().split()[0])
    except (IndexError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


class EDAFlowAdapter:
    """Run or parse a DC/Jasper SEC flow for one RTL candidate."""

    def __init__(self, config: EDAFlowConfig) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        source: str,
        version: str,
        timeout: float,
        work_dir: Path,
    ) -> EDAFlowResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        design_name = self.config.design_name or "candidate"
        source_path = work_dir / f"{design_name}.{version}.{self.config.file_ext}"
        source_path.write_text(source, encoding="utf-8")

        flow_root = Path(self.config.flow_root) if self.config.flow_root else work_dir
        flow_root.mkdir(parents=True, exist_ok=True)

        artifacts: dict[str, str] = {"rtl": str(source_path)}
        if self.config.golden_rtl:
            golden_src = Path(self.config.golden_rtl)
            if golden_src.exists():
                golden_dst = work_dir / f"{design_name}.v0.{golden_src.suffix.lstrip('.') or self.config.file_ext}"
                shutil.copyfile(golden_src, golden_dst)
                artifacts["golden_rtl"] = str(golden_dst)

        if not self.config.parse_only:
            command = self.config.flow_command or os.environ.get("VERILOG_EVOLVE_EDA_FLOW_CMD", "")
            if command:
                proc = self._run_flow_command(command, source_path=source_path, version=version, timeout=timeout, cwd=flow_root)
                artifacts["flow_log"] = str(work_dir / "eda_flow.log")
                (work_dir / "eda_flow.log").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
                if proc.returncode != 0:
                    return EDAFlowResult(
                        passed=False,
                        sec_passed=False,
                        result=f"EDA flow command failed with exit code {proc.returncode}",
                        feedback=["Check DC/Jasper logs before trusting PPA metrics."],
                        artifacts=artifacts,
                    )

        output_dir = flow_root / "output" / f"{design_name}.{version}"
        report_dir = flow_root / "reports" / f"{design_name}.{version}"
        ppa = _read_json(output_dir / "PPA_report.json")
        if not ppa:
            ppa = parse_ppa_report(report_dir)
        timing_word = _read_json(output_dir / "timing_word.json")
        critical_paths = self._load_critical_paths(timing_word, report_dir / "timing.rpt")
        sec_passed = self._load_sec_status(output_dir / "SEC_result.txt")

        metrics = {
            "eda_sec_passed": sec_passed,
            "sec_status": "pass" if sec_passed else "fail",
            "wns": _coerce_float(ppa.get("WNS", ppa.get("wns"))),
            "tns": _coerce_float(ppa.get("TNS", ppa.get("tns"))),
            "area": _coerce_float(ppa.get("Area", ppa.get("area"))),
            "power": _coerce_float(ppa.get("Power", ppa.get("power"))),
            "critical_paths": critical_paths,
        }
        if output_dir.exists():
            artifacts["output_dir"] = str(output_dir)
        if report_dir.exists():
            artifacts["report_dir"] = str(report_dir)

        missing = [name for name in ("wns", "tns", "area") if metrics.get(name) is None]
        if missing:
            return EDAFlowResult(
                passed=False,
                sec_passed=sec_passed,
                result=f"EDA reports missing metrics: {', '.join(missing)}",
                metrics=metrics,
                feedback=["Run the configured DC/Jasper flow or point flow_root at existing Dr. RTL-style reports."],
                artifacts=artifacts,
            )

        feedback = [
            f"SEC={'pass' if sec_passed else 'fail'}, WNS={metrics['wns']}, TNS={metrics['tns']}, area={metrics['area']}."
        ]
        return EDAFlowResult(
            passed=sec_passed,
            sec_passed=sec_passed,
            result="passed" if sec_passed else "SEC failed",
            metrics=metrics,
            feedback=feedback,
            artifacts=artifacts,
        )

    def _run_flow_command(self, command: str, *, source_path: Path, version: str, timeout: float, cwd: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "VE_RTL": str(source_path),
                "VE_VERSION": version,
                "VE_DESIGN": self.config.design_name,
                "VE_TOP": self.config.design_top,
                "VE_GOLDEN_RTL": self.config.golden_rtl,
                "VE_CLK": self.config.clock,
                "VE_RST": self.config.reset,
            }
        )
        return subprocess.run(command, cwd=cwd, env=env, shell=True, capture_output=True, text=True, timeout=max(timeout, 10.0))

    def _load_critical_paths(self, timing_word: dict[str, Any], timing_report: Path) -> list[dict[str, Any]]:
        if timing_word:
            paths = [
                {
                    "path": str(path),
                    "startpoint_word": str(path).split(" -> ", 1)[0],
                    "endpoint_word": str(path).split(" -> ", 1)[1] if " -> " in str(path) else "",
                    "slack": _coerce_float(slack),
                }
                for path, slack in timing_word.items()
            ]
            return sorted(paths, key=lambda item: float(item["slack"] if item["slack"] is not None else 0.0))
        return parse_timing_report(timing_report)

    def _load_sec_status(self, sec_path: Path) -> bool:
        if not sec_path.exists():
            return False
        text = sec_path.read_text(encoding="utf-8", errors="ignore").lower()
        return "passed" in text or re.search(r"^proven$", text, flags=re.MULTILINE) is not None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
