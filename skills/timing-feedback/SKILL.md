---
name: timing-feedback
description: Use when ABC timing proxy or critical-path feedback is part of the score. NOT for: tasks where only functional correctness matters.
category: verilog
---

## Timing-Friendly RTL Guidance

- Reduce long combinational chains by precomputing repeated conditions and balancing logic trees.
- For sequential datapaths, consider simple pipeline registers only when the task behavior allows the added cycle or already specifies registered outputs.
- Move mux selection close to the output when it removes control logic from arithmetic paths.
- Avoid priority chains unless priority is required; use `case` for mutually exclusive decodes.
- Keep multiplier and accumulator paths explicit and narrow; avoid accidental width growth.

## ABC Proxy Feedback

- A high `abc_delay_proxy` indicates too much mapped combinational logic or too many sequential elements in the inferred implementation.
- If logic cell count is high, first remove duplicated expressions before adding registers.
- If DFF count is high, check whether outputs or temporary values were over-registered.

