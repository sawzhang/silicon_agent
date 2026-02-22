"""Synchronize skill definitions from filesystem (skills/) into the database."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import SkillModel

logger = logging.getLogger(__name__)

_SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent / "skills"


def _parse_skill_md(path: Path) -> Optional[Dict]:
    """Parse a SKILL.md frontmatter (--- delimited YAML-like block) + body."""
    text = path.read_text(encoding="utf-8")
    # Extract frontmatter between --- markers
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return None

    front = match.group(1)
    body = match.group(2).strip()

    meta: Dict = {}
    for line in front.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            meta[key] = val

    name = meta.get("name")
    if not name:
        return None

    # Parse tags from metadata block
    tags: List[str] = []
    tags_match = re.search(r'tags:\s*\[(.*?)\]', front)
    if tags_match:
        raw = tags_match.group(1)
        tags = [t.strip().strip('"').strip("'") for t in raw.split(",")]

    return {
        "name": name,
        "description": meta.get("description", ""),
        "tags": tags,
        "content": body,
        "git_path": str(path.relative_to(_SKILLS_ROOT.parent)),
    }


async def sync_skills_from_filesystem(session: AsyncSession) -> Dict[str, str]:
    """Scan skills/ directory and upsert into DB. Returns {name: action} map."""
    if not _SKILLS_ROOT.exists():
        logger.info("Skills directory not found at %s, skipping sync", _SKILLS_ROOT)
        return {}

    results: Dict[str, str] = {}

    for role_dir in sorted(_SKILLS_ROOT.iterdir()):
        if not role_dir.is_dir() or role_dir.name == "shared":
            continue
        role = role_dir.name
        for skill_dir in sorted(role_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            parsed = _parse_skill_md(skill_md)
            if not parsed:
                logger.warning("Failed to parse %s", skill_md)
                continue

            skill_name = parsed["name"]
            result = await session.execute(
                select(SkillModel).where(SkillModel.name == skill_name)
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                skill = SkillModel(
                    name=skill_name,
                    display_name=parsed["description"][:200] if parsed["description"] else skill_name,
                    description=parsed["description"],
                    layer="L1",
                    tags=parsed["tags"],
                    applicable_roles=[role],
                    content=parsed["content"],
                    git_path=parsed["git_path"],
                    status="active",
                )
                session.add(skill)
                results[skill_name] = "created"
            else:
                # Update content if changed
                if existing.content != parsed["content"]:
                    existing.content = parsed["content"]
                    existing.git_path = parsed["git_path"]
                    results[skill_name] = "updated"
                else:
                    results[skill_name] = "unchanged"

    await session.commit()
    created = sum(1 for v in results.values() if v == "created")
    updated = sum(1 for v in results.values() if v == "updated")
    if created or updated:
        logger.info("Skill sync: %d created, %d updated", created, updated)
    return results
