"""Held-out randomized functional tests for downstream GEMM promotion gates."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .base import EvaluationResult
from .functional import FunctionalEvaluator


class HeldOutFunctionalEvaluator:
    """Run hidden randomized tests that are not exposed to repair prompts.

    The visible VerilogEval test remains useful for candidate repair. This
    evaluator is intended for promotion gating: a minor candidate can become a
    new major version only when it also passes these generated tests.
    """

    name = "heldout_functional"

    def __init__(self, *, samples: int = 64, seed: int = 0) -> None:
        self.samples = samples
        self.seed = seed
        self.functional = FunctionalEvaluator()

    def evaluate(
        self,
        *,
        problem: dict[str, Any],
        completion: str,
        timeout: float,
        work_dir: Path,
    ) -> EvaluationResult:
        task_id = str(problem.get("task_id", ""))
        test = build_gemm_heldout_test(task_id, samples=self.samples, seed=self.seed)
        if not test:
            return EvaluationResult(
                name=self.name,
                passed=True,
                result="skipped: no held-out generator for this task",
                metrics={"heldout_skipped": True, "heldout_samples": 0},
                feedback=["No task-specific held-out generator is available; promotion gate is neutral."],
            )

        hidden_problem = dict(problem)
        hidden_problem["test"] = test
        result = self.functional.evaluate(problem=hidden_problem, completion=completion, timeout=timeout, work_dir=work_dir)
        result.name = self.name
        result.metrics = {**result.metrics, "heldout_skipped": False, "heldout_samples": self.samples}
        result.feedback = [
            f"Held-out randomized GEMM test {'passed' if result.passed else 'failed'} with {self.samples} samples.",
            *result.feedback,
        ]
        return result


def build_gemm_heldout_test(task_id: str, *, samples: int = 64, seed: int = 0) -> str:
    rng = random.Random(f"{task_id}:{seed}:{samples}")
    if task_id == "int4_int8_mac_pe":
        vectors = [
            (rng.randint(-8, 7), rng.randint(-128, 127), rng.randint(-(2**20), 2**20 - 1), rng.randint(0, 4) != 0)
            for _ in range(samples)
        ]
        return _mac_pe_test(vectors)
    if task_id == "mixed_precision_dot4":
        vectors = [
            (
                rng.randint(-8, 7),
                rng.randint(-8, 7),
                rng.randint(-8, 7),
                rng.randint(-8, 7),
                rng.randint(-128, 127),
                rng.randint(-128, 127),
                rng.randint(-128, 127),
                rng.randint(-128, 127),
            )
            for _ in range(samples)
        ]
        return _dot4_test(vectors)
    if task_id == "requantize_int32_to_int8":
        vectors = [(rng.randint(-(2**24), 2**24 - 1), rng.randint(0, 15)) for _ in range(samples)]
        return _requant_test(vectors)
    return ""


def _mac_pe_test(vectors: list[tuple[int, int, int, bool]]) -> str:
    steps = "\n".join(
        f"    step({_sv_signed(4, act)}, {_sv_signed(8, weight)}, {_sv_signed(32, acc)}, 1'b{1 if valid else 0});"
        for act, weight, acc, valid in vectors
    )
    return f"""
module tb;
  reg clk, rst, valid_in;
  reg signed [3:0] act;
  reg signed [7:0] weight;
  reg signed [31:0] acc_in;
  wire valid_out;
  wire signed [31:0] acc_out;
  integer mismatches = 0;
  integer samples = 0;
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
{steps}
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
"""


def _dot4_test(vectors: list[tuple[int, int, int, int, int, int, int, int]]) -> str:
    checks = "\n".join(
        f"    check({_sv_signed(4, a0)}, {_sv_signed(4, a1)}, {_sv_signed(4, a2)}, {_sv_signed(4, a3)}, {_sv_signed(8, w0)}, {_sv_signed(8, w1)}, {_sv_signed(8, w2)}, {_sv_signed(8, w3)});"
        for a0, a1, a2, a3, w0, w1, w2, w3 in vectors
    )
    return f"""
module tb;
  reg signed [15:0] acts;
  reg signed [31:0] weights;
  wire signed [31:0] out;
  integer mismatches = 0;
  integer samples = 0;
  reg signed [31:0] exp;

  mixed_precision_dot4 dut(acts, weights, out);

  task check;
    input signed [3:0] a0;
    input signed [3:0] a1;
    input signed [3:0] a2;
    input signed [3:0] a3;
    input signed [7:0] w0;
    input signed [7:0] w1;
    input signed [7:0] w2;
    input signed [7:0] w3;
    begin
      acts = {{a3, a2, a1, a0}};
      weights = {{w3, w2, w1, w0}};
      exp = a0*w0 + a1*w1 + a2*w2 + a3*w3;
      #1;
      if (out !== exp) mismatches = mismatches + 1;
      samples = samples + 1;
    end
  endtask

  initial begin
{checks}
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
"""


def _requant_test(vectors: list[tuple[int, int]]) -> str:
    checks = "\n".join(f"    check({_sv_signed(32, value)}, 4'd{shift});" for value, shift in vectors)
    return f"""
module tb;
  reg signed [31:0] acc;
  reg [3:0] shift;
  wire signed [7:0] out;
  integer mismatches = 0;
  integer samples = 0;
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
{checks}
    $display("Mismatches: %0d in %0d samples", mismatches, samples);
    $finish;
  end
endmodule
"""


def _sv_signed(width: int, value: int) -> str:
    if value < 0:
        return f"-{width}'sd{abs(value)}"
    return f"{width}'sd{value}"
