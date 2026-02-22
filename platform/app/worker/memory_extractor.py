"""Extract reusable knowledge from completed task outputs and persist to project memory."""
from __future__ import annotations

import json
import logging
from typing import Dict, List

from app.config import settings
from app.worker.memory import CATEGORIES, MemoryEntry, ProjectMemoryStore

logger = logging.getLogger(__name__)


async def extract_and_store_memories(
    project_id: str,
    task_id: str,
    task_title: str,
    stage_outputs: List[Dict[str, str]],
) -> None:
    """Analyse all stage outputs and extract reusable memory entries.

    Calls LLM to identify conventions, architecture decisions, patterns
    and known issues.  Falls back to no-op when LLM is unavailable.
    """
    if not settings.MEMORY_ENABLED:
        return

    # Build a combined summary of all stage outputs (truncated to stay within context)
    combined = _build_combined_text(stage_outputs)
    if not combined.strip():
        return

    try:
        raw_entries = await _llm_extract(task_title, combined)
    except Exception:
        logger.warning(
            "LLM memory extraction failed for task %s, skipping",
            task_id,
            exc_info=True,
        )
        return

    if not raw_entries:
        return

    # Persist to file store
    store = ProjectMemoryStore(project_id)
    by_category: Dict[str, List[MemoryEntry]] = {}

    for item in raw_entries:
        cat = item.get("category", "").strip()
        content = item.get("content", "").strip()
        if cat not in CATEGORIES or not content:
            continue
        tags = item.get("tags", [])
        confidence = item.get("confidence", 1.0)
        entry = MemoryEntry.create(
            content=content,
            source_task_id=task_id,
            source_task_title=task_title,
            confidence=confidence,
            tags=tags if isinstance(tags, list) else [],
        )
        by_category.setdefault(cat, []).append(entry)

    for cat, entries in by_category.items():
        await store.add_entries(cat, entries)

    total = sum(len(v) for v in by_category.values())
    logger.info(
        "Extracted %d memory entries from task %s into %d categories",
        total, task_id, len(by_category),
    )


def _build_combined_text(stage_outputs: List[Dict[str, str]], max_chars: int = 8000) -> str:
    """Concatenate stage outputs, truncating if needed."""
    parts = []
    budget = max_chars
    for so in stage_outputs:
        header = f"## {so['stage']}\n"
        text = so.get("output", "")
        chunk = header + text
        if len(chunk) > budget:
            chunk = chunk[:budget] + "\n..."
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            break
    return "\n\n".join(parts)


async def _llm_extract(task_title: str, combined_text: str) -> List[dict]:
    """Call LLM to extract memory entries from stage outputs."""
    from app.integration.llm_client import ChatMessage, get_llm_client

    client = get_llm_client()
    prompt = (
        f"你是一个知识提取助手。以下是任务「{task_title}」的各阶段产出。\n\n"
        f"---\n{combined_text}\n---\n\n"
        "请从中提取可复用的知识条目（最多10条），按以下四个类别分类：\n"
        "- conventions: 编码规范、命名模式、代码风格约定\n"
        "- architecture: 设计决策、技术选型、架构模式\n"
        "- patterns: 可复用的实现方案、代码模式\n"
        "- issues: 已知问题、常见错误及修复方法\n\n"
        "请严格按以下 JSON 数组格式回复（不要添加 markdown 代码块标记）：\n"
        '[{"category": "conventions", "content": "一句话描述", "tags": ["tag1"], "confidence": 0.9}, ...]'
    )

    resp = await client.chat(
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0.3,
        max_tokens=2000,
    )

    try:
        data = json.loads(resp.content.strip())
        if isinstance(data, list):
            return data[:10]
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM memory extraction response")
    return []
