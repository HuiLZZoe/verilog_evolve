# Verilog-Evolve

Paper title: **Verilog-Evolve: Tool-Grounded and Skill-Evolving Verilog Generation**

`verilog-evolve` is a tool-grounded, versioned, and skill-evolving framework for generating Verilog from natural-language hardware tasks. It turns specification-to-RTL generation into a self-improvement loop: generate diverse candidates, evaluate them with executable feedback, promote better major versions, and evolve reusable RTL skills from run histories.

The current paper studies this system on VerilogEval and downstream-oriented mixed-precision GEMM tasks, with an emphasis on downstream-friendly Verilog rather than only one-shot functional correctness.

## Paper-Level Results

All Verilog-Evolve variants in the paper use the same search budget: 3 major rounds, 5 minor candidates per round, and the strategy pool `direct,c_bridge,repair`.

### VerilogEval Functional Stability

| Method | Tool Feedback | Machine Success | Human Success | Promotion Pass | Compile Pass |
| --- | --- | ---: | ---: | ---: | ---: |
| Direct LLM | none | 76.8 | 61.5 | 58.9 | 84.2 |
| C-Bridge | none | 79.4 | 64.7 | 61.8 | 86.5 |
| Repair-only | `iverilog`/`vvp` | 81.2 | 67.1 | 64.9 | 88.1 |
| Verilog-Evolve w/o Skill Evolution | `iverilog` + `yosys` + `abc` | 83.6 | 70.4 | 68.7 | 90.3 |
| Verilog-Evolve Full | `iverilog` + `yosys` + `abc` + skills | **85.3** | **72.8** | **71.6** | **91.9** |

Key takeaway: versioned tool-grounded selection improves final correctness and promotion stability, and skill evolution further improves the selected final candidates.

### Downstream-Aware GEMM Results

The GEMM experiment uses three built-in tasks: `int4_int8_mac_pe`, `mixed_precision_dot4`, and `requantize_int32_to_int8`. The downstream evaluator parses Yosys JSON netlists and reports structural metrics such as multiplier cells, DFF cells, and an area-delay-product proxy.

| Variant | Evaluators | Func. Pass | GEMM Held-out Pass | Cell Count | Mul Cells | ABC Delay | ADP Proxy | Downstream Score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Correctness-only | functional | 91.7 | 83.3 | 182.4 | 4.7 | 6.81 | 512.6 | 4.92 |
| PPA-aware | functional + Yosys | 91.7 | 86.1 | 159.8 | 3.8 | 6.43 | 428.4 | 4.31 |
| Timing-aware | functional + Yosys + ABC | 88.9 | 86.1 | 164.2 | 3.9 | 5.72 | 401.7 | 4.08 |
| Downstream-aware | functional + Yosys + ABC + downstream | **91.7** | **88.9** | **151.6** | **2.4** | **5.48** | **332.5** | **3.21** |

Key takeaway: the downstream-aware evaluator does more than count source-level operators. It uses netlist-level structure to select RTL with lower multiplier usage, lower timing proxy, better ADP proxy, and better GEMM-specific downstream score.

### Skill Evolution and Validation

| Skill Mode | Human Success | GEMM Score | GEMM Held-out Pass | Skill A/R |
| --- | ---: | ---: | ---: | ---: |
| No skills | 68.9 | 4.37 | 65.4 | -- |
| Static skills | 70.4 | 3.98 | 68.7 | -- |
| Evolved, immediate | 72.1 | 3.55 | 70.2 | 24/0 |
| Evolved, validated | **72.8** | **3.21** | **71.6** | 18/6 |

Key takeaway: reusable RTL guidance helps, but validation-gated skill evolution is more stable than publishing every verifier-approved update immediately.

## Core Idea

The updated flow uses simulator, synthesis, timing-proxy, downstream, and optional EDA results as learning signals:

```text
problem description + module declaration
  -> generate diverse candidates
  -> run selected evaluators: functional, Yosys, ABC, downstream, optional DC/SEC
  -> parse pass / mismatch / compile error / timeout / PPA / timing / netlist metrics
  -> score and record each minor candidate
  -> repair from the current major baseline when useful
  -> select the best minor in the round
  -> promote it to the next major version, optionally gated by held-out GEMM tests
  -> extract and evolve cross-task skill evidence
```

This is different from manual feedback loops: the repair prompt is grounded in tool output from `iverilog` and `vvp`, not human comments.

## Important Files

- `result_evolve.py`: result-grounded self-evolution runner.
- `runner.py`, `versioning.py`, `planning.py`, `generation.py`, `history.py`: modular implementation of the evolution loop.
- `evaluators/`: pluggable evaluator backends.
- `scoring.py`: configurable multi-objective scoring.
- `configs/*.json`: objective presets for correctness, PPA, timing, and downstream-aware runs.
- `prompts.py`: LLM client functions and generation/repair prompts.
- `skills/*/SKILL.md`: reusable Verilog generation and simulator-repair guidance injected into prompts.
- `skill_extractor.py`: evidence-backed SKILL.md extraction from history logs.
- `skill_evolver.py`, `skill_verifier.py`: SkillClaw-style `create_skill` / `improve_skill` / `skip` decisions with verifier reports and evidence artifacts.
- `skill_validation_worker.py`: external replay validation worker for queued skill publish jobs (`validated` mode).
- `downstream_tasks/quantized_gemm_pe.json`: lightweight quantized GEMM/PE downstream task spec.
- `verilog-eval/`: VerilogEval harness for `iverilog`/`vvp` functional checking.
- `run_agentv_c.py`: legacy C-bridge batch generator.
- `run_agentv.py`: legacy MyHDL/direct batch generator.
- `run_agent_aug.py`: legacy augmentation script for failed tasks.

## Dependencies

You need:

- Python 3.10+
- `iverilog` and `vvp` on `PATH`
- Optional: `yosys` for synthesis metrics and ABC timing proxy
- Python `openai` package for the current `prompts.py` client
- An OpenAI-compatible API configuration via environment variables (`VERILOG_EVOLVE_API_KEY` and optional `VERILOG_EVOLVE_BASE_URL` / `VERILOG_EVOLVE_MODEL`)

Install the local VerilogEval package if needed:

```bash
python3 -m pip install -e verilog-evolve/verilog-eval
python3 -m pip install openai
```

Recommended environment variables:

```bash
export VERILOG_EVOLVE_API_KEY=...
export VERILOG_EVOLVE_BASE_URL=https://api.deepseek.com/v1
export VERILOG_EVOLVE_MODEL=deepseek-coder
# optional: log prompt/response for reproducibility
export VERILOG_EVOLVE_LLM_LOG_DIR=./runs/llm_logs
```

## Run Result-Grounded Evolution

Run one task:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --rounds 3 \
  --candidates 5
```

Use the C-bridge strategy more aggressively:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --rounds 3 \
  --candidates 5 \
  --use-c-bridge
```

Run multiple tasks and write cross-task evidence for future skill updates:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --task-id edgedetect \
  --rounds 3 \
  --candidates 5 \
  --update-skill-evidence
```

Stop early when a full major round does not improve the current major baseline:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --rounds 5 \
  --candidates 5 \
  --stop-on-no-improvement
```

Run with Yosys synthesis metrics:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --evaluators functional,yosys \
  --score-config verilog-evolve/configs/ppa.json
```

Run with ABC timing proxy:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --evaluators functional,yosys,abc \
  --score-config verilog-evolve/configs/timing.json
```

Run a downstream-aware quantized GEMM/PE objective. The downstream evaluator uses Yosys JSON netlist metrics when available, including `mul_cells`, `add_cells`, `dff_cells`, `mux_cells`, `estimated_mac_lanes`, `pipeline_depth_proxy`, and `area_delay_product_proxy`.

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --evaluators functional,yosys,abc,downstream \
  --score-config verilog-evolve/configs/downstream.json \
  --downstream-spec verilog-evolve/downstream_tasks/quantized_gemm_pe.json \
  --heldout-tests
```

### Case: Open-Source Evaluation + GEMM Downstream Test

If you only want to use open-source tools (`iverilog`/`vvp`, `yosys`, and Yosys ABC) and use a GEMM-style task as the final downstream objective, use the `downstream` variant. This does not require DC/Jasper or `eda_dc_sec`.

First prepare the built-in mixed-precision GEMM benchmark:

```bash
python3 verilog-evolve/run_gemm_experiment.py --prepare-only
```

This writes:

```text
verilog-evolve/experiments/mixed_precision_gemm/data/GEMMEval.jsonl
verilog-evolve/experiments/mixed_precision_gemm/data/GEMMDescription.jsonl
```

Then run one GEMM downstream task, for example `mixed_precision_dot4`:

```bash
export VERILOG_EVOLVE_API_KEY=...
export VERILOG_EVOLVE_MODEL=deepseek-coder

python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/experiments/mixed_precision_gemm/data/GEMMEval.jsonl \
  --description-file verilog-evolve/experiments/mixed_precision_gemm/data/GEMMDescription.jsonl \
  --task-id mixed_precision_dot4 \
  --out-dir verilog-evolve/experiments/mixed_precision_gemm/runs/downstream \
  --rounds 3 \
  --candidates 5 \
  --strategies direct,c_bridge,repair \
  --evaluators functional,yosys,abc,downstream \
  --score-config verilog-evolve/configs/downstream.json \
  --downstream-spec verilog-evolve/downstream_tasks/quantized_gemm_pe.json \
  --heldout-tests \
  --heldout-samples 96 \
  --update-skill-evidence \
  --extract-skills \
  --evolve-skills
```

In this case:

```text
functional -> compile/simulate with iverilog/vvp
yosys      -> synthesis statistics such as cells, wires, and netlist structure
abc        -> lightweight timing proxy through Yosys ABC
downstream -> GEMM/PE-aware score from quantized-kernel structural features
heldout    -> randomized GEMM promotion tests, used only before promoting a major version
```

The final candidate is written to:

```text
verilog-evolve/experiments/mixed_precision_gemm/runs/downstream/best_samples.jsonl
verilog-evolve/experiments/mixed_precision_gemm/runs/downstream/mixed_precision_dot4/summary.json
```

For a quicker smoke test, reduce the search budget:

```bash
python3 verilog-evolve/run_gemm_experiment.py \
  --variants downstream \
  --task-id mixed_precision_dot4 \
  --rounds 1 \
  --candidates 2
```

## Mixed-Precision GEMM Experiment

`run_gemm_experiment.py` creates a small VerilogEval-compatible benchmark for LLM-inference-style mixed-precision GEMM building blocks:

- `int4_int8_mac_pe`
- `mixed_precision_dot4`
- `requantize_int32_to_int8`

Prepare only the benchmark files:

```bash
python3 verilog-evolve/run_gemm_experiment.py --prepare-only
```

Print the four experiment commands without launching LLM generation:

```bash
python3 verilog-evolve/run_gemm_experiment.py --dry-run
```

Run only the downstream-aware variant on one task:

```bash
python3 verilog-evolve/run_gemm_experiment.py \
  --variants downstream \
  --task-id mixed_precision_dot4 \
  --rounds 3 \
  --candidates 5
```

The script compares these variants:

```text
correctness -> functional
ppa         -> functional + Yosys
timing      -> functional + Yosys + ABC timing proxy
downstream  -> functional + Yosys + ABC timing proxy + quantized GEMM score
```

For GEMM tasks, the experiment script now enables held-out randomized promotion tests by default. Visible tests still drive repair feedback, while generated held-out tests gate major-version promotion.

Extract evidence-backed skills after a run:

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --rounds 3 \
  --candidates 5 \
  --update-skill-evidence \
  --extract-skills \
  --evolve-skills \
  --skill-min-support 2
```

Run validated skill publish mode (queue jobs instead of immediate publish):

```bash
python3 verilog-evolve/result_evolve.py \
  --problem-file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl \
  --description-file verilog-evolve/verilog-eval/descriptions/VerilogDescription_Human.jsonl \
  --task-id mux2to1 \
  --rounds 3 \
  --candidates 5 \
  --evolve-skills \
  --skill-publish-mode validated \
  --skill-validation-required-results 2 \
  --skill-validation-required-approvals 2 \
  --skill-validation-min-mean-score 0.75
```

Process queued validation jobs with external worker(s):

```bash
python3 verilog-evolve/skill_validation_worker.py \
  --skills-dir verilog-evolve/skills \
  --max-jobs 3 \
  --max-cases 3 \
  --timeout 30
```

Then run `result_evolve.py --evolve-skills --skill-publish-mode validated` again to finalize publish/reject based on worker results.

## Strategies

`result_evolve.py` cycles through strategies with `--strategies`:

```text
direct   -> generate Verilog directly with skill guidance
c_bridge -> generate C first, then Verilog
repair   -> repair the current best failing candidate using simulator feedback
```

Default:

```bash
--strategies direct,c_bridge,repair
```

You can override it:

```bash
--strategies c_bridge,direct,repair
```

## Outputs

By default, outputs go to `verilog-evolve/runs/result_evolve/`.

Per task:

```text
runs/result_evolve/<task_id>/
  candidates/
    v0.1.sv
    v0.2.sv
  major/
    v1.sv
    v2.sv
  history.jsonl
  history.json
  summary.json
```

`history.jsonl` records every minor attempt. `history.json` records DR_RTL-style major rounds:

```text
major round v0
  -> try v0.1, v0.2, ...
  -> select best minor
  -> promote to major/v1.sv when improved

major round v1
  -> try v1.1, v1.2, ...
  -> promote to major/v2.sv when improved
```

Global:

```text
runs/result_evolve/
  best_samples.jsonl
  run_summary.json
```

If `--update-skill-evidence` is set:

```text
skills/evidence/
  run_evidence.json
  run_evidence.md
```

If `--extract-skills` is set:

```text
skills/auto-extracted/
  SKILL.md
  evidence.json
runs/result_evolve/
  skill_extraction.json
```

If `--evolve-skills` is set, the runner writes SkillClaw-style skill evolution decisions and per-candidate artifacts:

```text
runs/result_evolve/
  skill_evolution_decisions.json
  skill_evolution/
    <candidate_skill_key>/
      old_skill_snapshot.md
      candidate_skill.md
      candidate_skill.diff
      evidence.json
      verifier_report.json
```

Accepted `create_skill` / `improve_skill` decisions are published back into `skills/<candidate_name>/SKILL.md`.

If `--skill-publish-mode validated` is used, additional worker-state artifacts are created:

```text
skills/.evolver_store/
  evolve_skill_registry.json
  validation_jobs.jsonl
  sessions/pending/
runs/result_evolve/
  run_context.json
```

`validation_jobs.jsonl` is append/update-safe for multiple external worker invocations. Jobs stay `pending` until they accumulate enough worker results to satisfy thresholds.

`best_samples.jsonl` can be passed back into VerilogEval:

```bash
evaluate_functional_correctness \
  verilog-evolve/runs/result_evolve/best_samples.jsonl \
  --problem_file verilog-evolve/verilog-eval/data/VerilogEval_Human.jsonl
```

## Self-Evolution Model

The current implementation follows a DR_RTL-style versioned search loop and a SkillClaw-style skill evolution loop:

- It keeps reusable skills in `skills/*/SKILL.md`.
- It injects those skills into generation and repair prompts.
- It records each candidate in `history.jsonl`.
- It promotes the best minor candidate into `major/vN.sv`, following the DR_RTL-style versioned optimization loop.
- When `--heldout-tests` is enabled, promotion also requires generated held-out/randomized tests to pass.
- It parses simulator output into failure kinds and repair hints.
- It can include Yosys cell/wire metrics, ABC delay proxy, and a downstream quantized GEMM/PE score backed by Yosys JSON netlist metrics.
- It can write aggregated evidence under `skills/evidence/`.
- It can conservatively extract repeated guidance into `skills/auto-extracted/SKILL.md`.
- It can evolve skills with `create_skill` / `improve_skill` / `skip` decisions, old-skill snapshots, candidate diffs, evidence files, and verifier reports.
- In `validated` publish mode, skill release is gated by external replay validation workers rather than internal auto-accept heuristics.

The intended workflow is:

1. Run a batch with `--update-skill-evidence`.
2. Inspect `skills/evidence/run_evidence.md`.
3. Run `--extract-skills` for repeated simulator/synthesis repair guidance.
4. Run `--evolve-skills --skill-publish-mode validated` to queue candidate skills.
5. Run one or more `skill_validation_worker.py` processes to replay and score queued jobs.
6. Re-run `--evolve-skills --skill-publish-mode validated` to finalize publish/reject from worker results.
7. Re-run the benchmark and compare pass rate, downstream score, and promotion stability.

