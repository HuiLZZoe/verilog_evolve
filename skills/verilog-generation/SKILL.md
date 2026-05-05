---
name: verilog-generation
description: Use when generating VerilogEval or HDLBits-style Verilog modules from a problem description and module declaration. NOT for: optimizing an existing RTL design for timing or area.
category: verilog
---

## Core Rules

- Preserve the required module declaration exactly: module name, port names, widths, and directions must match the harness.
- Return only the design module. Do not include a testbench, markdown, explanations, or extra modules unless the task explicitly requires submodules.
- For combinational logic, use `always @(*)` with default assignments for every assigned output or use continuous `assign`.
- For sequential logic, update state only on the specified clock edge and implement reset polarity exactly as described.
- Avoid SystemVerilog-only constructs unless the task or harness clearly supports them. Prefer Verilog-2005-compatible `reg`, `wire`, `localparam`, and `case`.

## Common HDLBits Patterns

- Edge detectors usually need a delayed copy of the previous input sampled on the clock edge.
- Sequence detectors usually need explicit state registers, reset behavior, and a default next-state path.
- Counters should preserve the specified wrap, saturate, enable, and reset semantics; do not change count direction unless the description says so.
- FSM outputs must match whether the problem asks for Moore behavior, Mealy behavior, or registered outputs.
- Signed arithmetic must declare operands or intermediate wires as `signed` when the behavior depends on signed comparison or extension.

