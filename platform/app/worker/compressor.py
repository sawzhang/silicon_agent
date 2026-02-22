"""L0/L1/L2 stage output compression for sliding-window context injection.

L0 — one-line summary   (~100 tokens)  — for distant stages (distance ≥ 2)
L1 — bullet-point brief (~500 tokens)  — for next-nearest stage (distance = 1)
L2 — original full text (unlimited)    — for the immediately preceding stage (distance = 0)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Fallback truncation limits when LLM is unavailable
_L0_FALLBACK_CHARS = 200
_L1_FALLBACK_CHARS = 1500


@dataclass
class CompressedOutput:
    """Holds all compression levels for a single stage output."""
    stage_name: str
    l0: str  # one-line summary
    l1: str  # structured bullet points
    l2: str  # original full text


@dataclass
class CompressionResult:
    """Accumulated compressed outputs for all completed stages."""
    outputs: List[CompressedOutput] = field(default_factory=list)

    def add(self, co: CompressedOutput) -> None:
        self.outputs.append(co)

    def build_prior_context(self, current_index: int) -> List[Dict[str, str]]:
        """Build prior_outputs list with sliding-window compression.

        current_index: the 0-based index of the stage about to execute.
        Returns list of {"stage": name, "output": text} using appropriate level.
        """
        result: List[Dict[str, str]] = []
        for i, co in enumerate(self.outputs):
            distance = current_index - i - 1  # how far back this stage is
            if distance <= 0:
                # Immediately preceding stage → full text
                text = co.l2
            elif distance == 1:
                # Next-nearest → L1 bullet points
                text = f"[摘要]\n{co.l1}"
            else:
                # Distant → L0 one-liner
                text = f"[概要] {co.l0}"
            result.append({"stage": co.stage_name, "output": text})
        return result


async def compress_stage_output(
    stage_name: str,
    output: str,
) -> CompressedOutput:
    """Compress a stage output into L0/L1/L2 levels.

    Uses LLM when available and compression is enabled; falls back to truncation.
    """
    if not settings.MEMORY_COMPRESSION_ENABLED:
        return CompressedOutput(
            stage_name=stage_name,
            l0=_fallback_l0(output),
            l1=_fallback_l1(output),
            l2=output,
        )

    try:
        l0, l1 = await _llm_compress(stage_name, output)
        return CompressedOutput(stage_name=stage_name, l0=l0, l1=l1, l2=output)
    except Exception:
        logger.warning(
            "LLM compression failed for stage %s, using fallback", stage_name,
            exc_info=True,
        )
        return CompressedOutput(
            stage_name=stage_name,
            l0=_fallback_l0(output),
            l1=_fallback_l1(output),
            l2=output,
        )


async def _llm_compress(stage_name: str, output: str) -> tuple[str, str]:
    """Call LLM to generate L0 and L1 summaries."""
    from app.integration.llm_client import ChatMessage, get_llm_client

    client = get_llm_client()
    prompt = (
        f"你是一个技术文档压缩助手。请对以下【{stage_name}】阶段的产出进行两级压缩：\n\n"
        f"---\n{output[:6000]}\n---\n\n"
        "请严格按以下 JSON 格式回复（不要添加 markdown 代码块标记）：\n"
        '{"l0": "一句话概括该阶段产出（不超过50字）", '
        '"l1": "要点摘要，用换行分隔的3-5个要点（每个要点不超过80字）"}'
    )
    resp = await client.chat(
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0.3,
        max_tokens=600,
    )

    import json
    try:
        data = json.loads(resp.content.strip())
        return data["l0"], data["l1"]
    except (json.JSONDecodeError, KeyError):
        # If JSON parsing fails, try to extract what we can
        logger.warning("Failed to parse LLM compression response, using fallback")
        return _fallback_l0(output), _fallback_l1(output)


def _fallback_l0(output: str) -> str:
    """Fallback L0: first line, truncated."""
    first_line = output.split("\n", 1)[0].strip()
    if len(first_line) > _L0_FALLBACK_CHARS:
        return first_line[:_L0_FALLBACK_CHARS] + "..."
    return first_line or output[:_L0_FALLBACK_CHARS].strip()


def _fallback_l1(output: str) -> str:
    """Fallback L1: first N characters."""
    if len(output) <= _L1_FALLBACK_CHARS:
        return output
    return output[:_L1_FALLBACK_CHARS] + "\n..."
