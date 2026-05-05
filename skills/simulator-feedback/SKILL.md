---
name: simulator-feedback
description: Use when repairing Verilog from iverilog/vvp compile, timeout, or mismatch feedback. NOT for: cosmetic rewrites when the candidate already passes.
category: verilog
---

## Repair Priorities

- Fix compile errors before functional mismatches. A candidate that does not compile gives weak behavioral evidence.
- If the error says an output is not a valid l-value, make that port an `output reg` when allowed by the declaration, or drive an internal `reg` and continuously assign it to the output.
- If Icarus reports variable declarations inside unnamed blocks, move `integer`, `reg`, and `wire` declarations to module scope or the named block scope.
- If a for loop is incomprehensible, declare the loop variable at module scope and use static bounds.
- If the simulation times out, look for combinational feedback, missing default assignments, or state machines that never make progress.

## Mismatch Debugging

- Treat `failed: X out of Y samples` as functional feedback, not syntax feedback.
- First check reset polarity, reset value, and whether reset is synchronous or asynchronous.
- Then check clock edge, nonblocking assignments in sequential blocks, and whether outputs should be registered or combinational.
- For combinational tasks, ensure every branch assigns every output to avoid unintended latches.
- For bit-vector tasks, check indexing direction, concatenation order, signedness, and width truncation/extension.

