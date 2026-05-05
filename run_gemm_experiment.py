#!/usr/bin/env python3
"""Prepare and run a mixed-precision GEMM/PE downstream experiment.

The script creates a small VerilogEval-compatible benchmark for quantized GEMM
building blocks, then optionally launches result_evolve.py under several
objectives:

- correctness: functional only
- ppa: functional + Yosys
- timing: functional + Yosys + ABC proxy
- downstream: functional + Yosys + ABC proxy + quantized GEMM objective

It is intentionally lightweight: the generated benchmark focuses on PE/MAC
micro-kernels that represent LLM inference GEMM tiles.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_EXP_DIR = ROOT / "experiments" / "mixed_precision_gemm"


def _jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _mac_pe_test() -> str:
    return r'''
module tb;
  reg clk, rst, valid_in;
  reg signed [3:0] act;
  reg signed [7:0] weight;
  reg signed [31:0] acc_in;
  wire valid_out;
  wire signed [31:0] acc_out;
  integer mismatches = 0;
  integer samples = 0;
  integer i;
  reg exp_valid;
  reg signed [31:0] exp_acc;

  int4_int8_mac_pe dut(clk, rst, valid_in, act, weight, acc_in, valid_out, acc_out);
  always #5 clk = ~clk;

  task step;
    input signed [3:0] a;
    input signed [7:0] w;
    input signed [31:0] acc;
    input v;
    begin
      act = a; weight = w; acc_in = acc; valid_in = v;
      exp_valid = v;
      exp_acc = v ? (acc + a * w) : acc_out;
      @(posedge clk); #1;
      if (valid_out !== exp_valid) mismatches = mismatches + 1;
      if (v && acc_out !== exp_acc) mismatches = mismatches + 1;
      samples = samples + 1;
    end
  endtask

  initial begin
    clk = 0; rst = 1; valid_in = 0; act = 0; weight = 0; acc_in = 0;
    repeat (2) @(posedge clk); #1;
    rst = 0;
    step(4'sd3, 8'sd7, 32'sd10, 1'b1);
    step(-4'sd2, 8'sd9, 32'sd5, 1'b1);
    step(4'sd1, -8'sd8, 32'sd20, 1'b1);
    step(4'sd0, 8'sd12, 32'sd99, 1'b0);
    for (i = -8; i < 8; i = i + 1) begin
      step(i[3:0], (i * 3), i * 11, 1'b1);
    end
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
'''


def _dot4_test() -> str:
    return r'''
module tb;
  reg signed [15:0] acts;
  reg signed [31:0] weights;
  wire signed [31:0] out;
  integer mismatches = 0;
  integer samples = 0;
  integer i;
  reg signed [3:0] a0, a1, a2, a3;
  reg signed [7:0] w0, w1, w2, w3;
  reg signed [31:0] exp;

  mixed_precision_dot4 dut(acts, weights, out);

  task check;
    input signed [3:0] ta0, ta1, ta2, ta3;
    input signed [7:0] tw0, tw1, tw2, tw3;
    begin
      acts = {ta3, ta2, ta1, ta0};
      weights = {tw3, tw2, tw1, tw0};
      exp = ta0*tw0 + ta1*tw1 + ta2*tw2 + ta3*tw3;
      #1;
      if (out !== exp) mismatches = mismatches + 1;
      samples = samples + 1;
    end
  endtask

  initial begin
    check(1, 2, 3, 4, 5, 6, 7, 8);
    check(-1, 2, -3, 4, 8, -7, 6, -5);
    check(7, -8, 0, 1, -2, 3, -4, 5);
    for (i = 0; i < 16; i = i + 1) begin
      a0 = i[3:0]; a1 = (i+1); a2 = -(i[2:0]); a3 = (i-3);
      w0 = i*2; w1 = -i; w2 = i+5; w3 = 7-i;
      check(a0, a1, a2, a3, w0, w1, w2, w3);
    end
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
'''


def _requant_test() -> str:
    return r'''
module tb;
  reg signed [31:0] acc;
  reg [3:0] shift;
  wire signed [7:0] out;
  integer mismatches = 0;
  integer samples = 0;
  integer i;
  reg signed [31:0] shifted;
  reg signed [7:0] exp;

  requantize_int32_to_int8 dut(acc, shift, out);

  task check;
    input signed [31:0] value;
    input [3:0] sh;
    begin
      acc = value; shift = sh;
      shifted = value >>> sh;
      if (shifted > 127) exp = 127;
      else if (shifted < -128) exp = -128;
      else exp = shifted[7:0];
      #1;
      if (out !== exp) mismatches = mismatches + 1;
      samples = samples + 1;
    end
  endtask

  initial begin
    check(32'sd1024, 4'd3);
    check(-32'sd1024, 4'd3);
    check(32'sd999999, 4'd4);
    check(-32'sd999999, 4'd4);
    for (i = -20; i < 20; i = i + 1) begin
      check(i * 37, i[3:0]);
    end
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
'''


def build_benchmark() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    problems = [
        {
            "task_id": "int4_int8_mac_pe",
            "prompt": "module int4_int8_mac_pe(input clk, input rst, input valid_in, input signed [3:0] act, input signed [7:0] weight, input signed [31:0] acc_in, output reg valid_out, output reg signed [31:0] acc_out);\n",
            "test": _mac_pe_test(),
        },
        {
            "task_id": "mixed_precision_dot4",
            "prompt": "module mixed_precision_dot4(input signed [15:0] acts, input signed [31:0] weights, output signed [31:0] out);\n",
            "test": _dot4_test(),
        },
        {
            "task_id": "requantize_int32_to_int8",
            "prompt": "module requantize_int32_to_int8(input signed [31:0] acc, input [3:0] shift, output signed [7:0] out);\n",
            "test": _requant_test(),
        },
    ]
    descriptions = [
        {
            "task_id": "int4_int8_mac_pe",
            "simple_description": "Build a pipelined mixed-precision MAC processing element.",
            "detail_description": "On each rising clock edge, if rst is high, clear valid_out and acc_out. Otherwise valid_out follows valid_in. When valid_in is high, acc_out becomes acc_in + act * weight using signed INT4 activation and signed INT8 weight, accumulating into signed INT32. The implementation should be friendly to low-area quantized GEMM hardware.",
        },
        {
            "task_id": "mixed_precision_dot4",
            "simple_description": "Build a 4-lane mixed-precision dot-product combinational block.",
            "detail_description": "The input acts packs four signed INT4 values as {a3,a2,a1,a0}. The input weights packs four signed INT8 values as {w3,w2,w1,w0}. Output signed INT32 should equal a0*w0 + a1*w1 + a2*w2 + a3*w3. Preserve signed arithmetic and explicit quantized bit widths.",
        },
        {
            "task_id": "requantize_int32_to_int8",
            "simple_description": "Build an INT32-to-INT8 requantization block.",
            "detail_description": "Arithmetic right shift the signed INT32 accumulator by shift, then saturate the result to signed INT8 range [-128, 127]. This represents a low-cost post-accumulation quantization stage for LLM inference GEMM kernels.",
        },
    ]
    return problems, descriptions


def prepare_benchmark(exp_dir: Path) -> tuple[Path, Path]:
    problems, descriptions = build_benchmark()
    data_dir = exp_dir / "data"
    problem_file = data_dir / "GEMMEval.jsonl"
    description_file = data_dir / "GEMMDescription.jsonl"
    _jsonl_write(problem_file, problems)
    _jsonl_write(description_file, descriptions)
    return problem_file, description_file


def run_variant(
    *,
    name: str,
    problem_file: Path,
    description_file: Path,
    exp_dir: Path,
    rounds: int,
    candidates: int,
    task_ids: list[str],
    dry_run: bool,
) -> None:
    variants = {
        "correctness": {
            "evaluators": "functional",
            "score_config": ROOT / "configs" / "correctness.json",
            "extra": [],
        },
        "ppa": {
            "evaluators": "functional,yosys",
            "score_config": ROOT / "configs" / "ppa.json",
            "extra": [],
        },
        "timing": {
            "evaluators": "functional,yosys,abc",
            "score_config": ROOT / "configs" / "timing.json",
            "extra": [],
        },
        "downstream": {
            "evaluators": "functional,yosys,abc,downstream",
            "score_config": ROOT / "configs" / "downstream.json",
            "extra": ["--downstream-spec", str(ROOT / "downstream_tasks" / "quantized_gemm_pe.json")],
        },
    }
    cfg = variants[name]
    out_dir = exp_dir / "runs" / name
    cmd = [
        sys.executable,
        str(ROOT / "result_evolve.py"),
        "--problem-file",
        str(problem_file),
        "--description-file",
        str(description_file),
        "--out-dir",
        str(out_dir),
        "--rounds",
        str(rounds),
        "--candidates",
        str(candidates),
        "--evaluators",
        cfg["evaluators"],
        "--score-config",
        str(cfg["score_config"]),
        "--strategies",
        "direct,c_bridge,repair",
        "--stop-on-no-improvement",
        "--heldout-tests",
        "--heldout-samples",
        "96",
        "--update-skill-evidence",
        "--extract-skills",
        "--evolve-skills",
        *cfg["extra"],
    ]
    for task_id in task_ids:
        cmd.extend(["--task-id", task_id])

    print(" ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and run mixed-precision GEMM Verilog-Evolve experiments.")
    parser.add_argument("--exp-dir", default=str(DEFAULT_EXP_DIR), help="Experiment output directory")
    parser.add_argument("--prepare-only", action="store_true", help="Only generate benchmark jsonl files")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running result_evolve.py")
    parser.add_argument("--variants", default="correctness,ppa,timing,downstream", help="Comma-separated variants")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--candidates", type=int, default=5)
    parser.add_argument("--task-id", action="append", help="Optional task subset; can be repeated")
    args = parser.parse_args()

    exp_dir = Path(args.exp_dir)
    problem_file, description_file = prepare_benchmark(exp_dir)
    print(f"Wrote {problem_file}")
    print(f"Wrote {description_file}")
    if args.prepare_only:
        return

    default_tasks = [row["task_id"] for row in build_benchmark()[0]]
    task_ids = args.task_id or default_tasks
    for variant in [item.strip() for item in args.variants.split(",") if item.strip()]:
        run_variant(
            name=variant,
            problem_file=problem_file,
            description_file=description_file,
            exp_dir=exp_dir,
            rounds=args.rounds,
            candidates=args.candidates,
            task_ids=task_ids,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
