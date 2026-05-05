---
name: downstream-co-design
description: Use when generating downstream-friendly RTL for quantized GEMM or LLM inference kernels. NOT for: generic HDLBits tasks with no PPA or downstream objective.
category: verilog
---

## Quantized GEMM / PE Guidance

- Preserve quantized bit widths explicitly. Prefer narrow INT4/INT8 inputs, wider INT16/INT32 accumulators, and clear sign extension when signed math is required.
- Keep multiplier usage within the downstream budget. If possible, reuse one multiplier across cycles or separate lanes with explicit control.
- Make accumulation width large enough to avoid overflow for the intended dot-product depth.
- Use simple valid/enable handshakes for pipelined MAC structures.
- Separate datapath and control logic so synthesis and timing tools can optimize each part.

## Co-Design Hints

- A lower downstream score prefers fewer multipliers, preferred quantized bitwidth patterns, and simple pipeline blocks.
- Do not improve area by changing numerical semantics; quantization behavior and accumulator width are part of the algorithm contract.

