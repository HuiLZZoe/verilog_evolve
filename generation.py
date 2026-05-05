"""Description loading, skill retrieval, and LLM generation/repair calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VERILOG_EVAL_ROOT = ROOT / "verilog-eval"
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(VERILOG_EVAL_ROOT) not in sys.path:
    sys.path.append(str(VERILOG_EVAL_ROOT))

from skills_runtime import retrieve_skill_guidance  # noqa: E402
from utils import check_empty, get_module_complete  # noqa: E402
from verilog_eval.data import stream_jsonl  # noqa: E402
from versioning import Evaluation  # noqa: E402


def load_descriptions(description_file: str) -> dict[str, dict[str, Any]]:
    descriptions: dict[str, dict[str, Any]] = {}
    for row in stream_jsonl(description_file):
        descriptions[row["task_id"]] = row
    return descriptions


def get_description(row: dict[str, Any]) -> str:
    detail = str(row.get("detail_description", "")).strip()
    simple = str(row.get("simple_description", "")).strip()
    if simple and detail:
        return f"{simple}\n{detail}"
    return simple or detail


def normalize_completion(generated_verilog: str) -> str:
    completion = get_module_complete(generated_verilog).strip()
    if not completion or check_empty(generated_verilog):
        return generated_verilog.strip()
    return completion


def load_skill_guidance(
    skills_dir: str,
    max_chars: int = 6000,
    *,
    task_description: str = "",
    timing_paths: list[dict[str, Any]] | None = None,
    optimization_plan: dict[str, Any] | None = None,
) -> str:
    """Load lightweight SkillClaw-style SKILL.md guidance for prompt injection."""
    if task_description or timing_paths or optimization_plan:
        return retrieve_skill_guidance(
            skills_dir,
            task_description=task_description,
            timing_paths=timing_paths,
            optimization_plan=optimization_plan,
            max_chars=max_chars,
        )
    root = Path(skills_dir)
    if not root.exists():
        return ""

    chunks: list[str] = []
    for skill_path in sorted(root.glob("*/SKILL.md")):
        try:
            text = skill_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        chunks.append(f"# {skill_path.parent.name}\n{text}")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    return "\n\n".join(chunks)[:max_chars]


def load_generation_api() -> dict[str, Any]:
    """Import LLM prompt helpers lazily so CLI/help work without OpenAI installed."""
    try:
        from prompts import (  # noqa: PLC0415
            c_llm,
            c_prompt,
            crc_llm,
            key_llm,
            key_prompt,
            result_repair_prompt,
            run_agent,
            v_llm,
            v_prompt,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "openai":
            raise RuntimeError(
                "The generation loop requires the 'openai' package because verilog-evolve/prompts.py "
                "uses an OpenAI-compatible client. Install it in this environment before running "
                "candidate generation."
            ) from exc
        raise

    return {
        "c_llm": c_llm,
        "c_prompt": c_prompt,
        "crc_llm": crc_llm,
        "key_llm": key_llm,
        "key_prompt": key_prompt,
        "result_repair_prompt": result_repair_prompt,
        "run_agent": run_agent,
        "v_llm": v_llm,
        "v_prompt": v_prompt,
    }


def generate_candidate(
    description: str,
    head: str,
    strategy: str,
    skill_guidance: str,
) -> tuple[str, dict[str, Any]]:
    api = load_generation_api()
    prompt_description = description
    if skill_guidance:
        prompt_description = f"{description}\n\nReusable Verilog generation skills:\n{skill_guidance}"

    if strategy == "direct":
        generated = api["run_agent"](
            api["crc_llm"],
            api["result_repair_prompt"],
            [prompt_description, head, "", "initial generation", skill_guidance],
            lang="verilog",
        )
        return normalize_completion(generated), {"generator": "direct_result_prompt", "strategy": strategy}

    key_points = api["run_agent"](api["key_llm"], api["key_prompt"], [prompt_description, head], lang=None)
    description_key = f"{prompt_description}\n{key_points}"
    generated_c = api["run_agent"](api["c_llm"], api["c_prompt"], [description_key, head], lang="c")
    generated_v = api["run_agent"](api["v_llm"], api["v_prompt"], [description_key, head, generated_c], lang="verilog")
    return normalize_completion(generated_v), {
        "generator": "c_bridge",
        "strategy": strategy,
        "key_points": key_points,
        "c_preview": generated_c[:1000],
    }


def repair_candidate(
    description: str,
    head: str,
    completion: str,
    evaluation: Evaluation,
    skill_guidance: str,
) -> tuple[str, dict[str, Any]]:
    api = load_generation_api()
    failure_summary = {
        "result": evaluation.result,
        "failure_kind": evaluation.failure_kind,
        "mismatch_count": evaluation.mismatch_count,
        "sample_count": evaluation.sample_count,
        "score": evaluation.score,
        "analysis": evaluation.analysis,
    }
    prompt_description = description
    if skill_guidance:
        prompt_description = f"{description}\n\nReusable Verilog repair skills:\n{skill_guidance}"
    generated = api["run_agent"](
        api["crc_llm"],
        api["result_repair_prompt"],
        [prompt_description, head, completion, json.dumps(failure_summary, indent=2), skill_guidance],
        lang="verilog",
    )
    return normalize_completion(generated), {"generator": "result_repair", "strategy": "repair", "feedback": failure_summary}
