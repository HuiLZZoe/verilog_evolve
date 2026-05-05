---
name: rtl-opt
description: Evidence-aware RTL timing/PPA optimization patterns. Use when timing paths, SEC, WNS/TNS/area, fanout, mux, decode, FSM, or synthesis feedback is available.
category: verilog
---

## Use Conditions

Use this skill only when optimization must preserve the original module interface, reset behavior, and pipeline latency. SEC or an equivalent formal check is the hard gate for promotion.

## High-Confidence Patterns

### One-Hot Pre-Decode for Control Signals

- Pattern: FSM/opcode/state signals feed repeated comparisons or mux selects.
- Strategy: Extract reusable one-hot wires such as `state_is_idle`, `op_is_add`, or `count_is_max`.
- Evidence schema: require `sec_pass >= 2` and at least one promoted candidate before treating as verified in this project.
- Risk: low for combinational predecode; do not move state updates across clock boundaries.

### Condition Pre-Computation and CSE

- Pattern: repeated comparisons, counter terminal checks, or shared arithmetic subexpressions.
- Strategy: compute the condition once as a named wire and reuse it across next-state and datapath logic.
- Evidence schema: track WNS/TNS/area deltas against the current major version.
- Risk: low, but signedness and width must be explicit.

### Mux-Before-Adder Restructuring

- Pattern: `sel ? (A + B) : (C + D)` or equivalent duplicated arithmetic behind a late mux.
- Strategy: rewrite as `(sel ? A : C) + (sel ? B : D)` when widths and signedness are identical.
- Evidence schema: require SEC pass because this transformation can subtly change overflow behavior.
- Risk: medium.

### Late Mux Select with Unconditional Computation

- Pattern: control signal is on a critical arithmetic path.
- Strategy: compute candidate arithmetic results in parallel, then select late with a narrow mux.
- Evidence schema: accept only if area growth stays below the configured threshold or the score improves.
- Risk: medium to high area increase.

### Hierarchical Case Decomposition

- Pattern: large encoder/decoder/case table on a critical path.
- Strategy: split into smaller structured sub-decoders when the encoding has natural hierarchy.
- Evidence schema: require SEC pass and a promoted candidate because manual table edits are error-prone.
- Risk: medium.

## Anti-Patterns

- Do not add or remove pipeline stages unless the formal flow is explicitly configured to prove the changed sequential relationship.
- Do not change public ports, reset polarity, clocking, or task-visible latency.
- Do not optimize a path by deleting rarely-used behavior; tests and SEC must remain the source of truth.
- Do not trust ABC proxy timing as a SOTA metric when DC/OpenSTA/Jasper data is available.

## Evidence Fields To Record

- `sec_pass`: number of equivalent candidates using this pattern.
- `promoted`: number of major promotions using this pattern.
- `avg_wns_delta`, `avg_tns_delta`, `avg_area_delta`: deltas vs the parent major version.
- `failed_reasons`: SEC failures, syntax failures, area blow-ups, or functional mismatches.
