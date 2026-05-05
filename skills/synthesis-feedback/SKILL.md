---
name: synthesis-feedback
description: Use when Yosys synthesis metrics are part of the score. NOT for: pure functional repair when synthesis is disabled.
category: verilog
---

## Area-Friendly RTL Guidance

- Prefer shared comparators, shared adders, and simple mux structures when the description allows it.
- Avoid duplicating large arithmetic expressions in multiple branches; compute common terms once with wires.
- Replace deeply nested ternary chains with a clear `case` or staged mux tree when it improves readability and synthesis sharing.
- Keep state encodings simple unless one-hot encoding is clearly beneficial for the task.
- Avoid unnecessary registers in combinational tasks; avoid unnecessary combinational duplication in sequential tasks.

## Yosys Feedback

- High `cell_count` usually means duplicated arithmetic, overly broad case logic, or parallel operators that can be shared.
- High `wire_bits` often indicates oversized intermediate signals or unnecessary wide datapaths.
- If Yosys fails, simplify unsupported constructs and prefer Verilog-2005-compatible idioms.

