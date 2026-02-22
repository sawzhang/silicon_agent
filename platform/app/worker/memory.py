"""Project memory store: file-based per-project knowledge persistence.

Stores reusable knowledge extracted from completed tasks, organised into
four categories: conventions, architecture, patterns, issues.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Per-project write locks to prevent concurrent file corruption
_project_locks: Dict[str, asyncio.Lock] = {}

# Categories
CATEGORIES = ("conventions", "architecture", "patterns", "issues")

# Role → categories visible to that role
ROLE_MEMORY_ACCESS: Dict[str, List[str]] = {
    "orchestrator": ["conventions", "architecture", "patterns", "issues"],
    "spec":         ["conventions", "architecture"],
    "coding":       ["conventions", "patterns", "issues"],
    "test":         ["patterns", "issues"],
    "review":       ["conventions", "architecture", "issues"],
    "smoke":        ["architecture", "issues"],
    "doc":          ["conventions", "architecture"],
}

_MEMORY_ROOT = Path(__file__).resolve().parent.parent.parent / "memory"


@dataclass
class MemoryEntry:
    id: str
    content: str
    source_task_id: str
    source_task_title: str
    created_at: str  # ISO format
    confidence: float = 1.0
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def create(
        content: str,
        source_task_id: str,
        source_task_title: str,
        confidence: float = 1.0,
        tags: Optional[List[str]] = None,
    ) -> "MemoryEntry":
        return MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            source_task_id=source_task_id,
            source_task_title=source_task_title,
            created_at=datetime.now(timezone.utc).isoformat(),
            confidence=confidence,
            tags=tags or [],
        )


def _get_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]


class ProjectMemoryStore:
    """File-backed memory store for a single project."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.root = _MEMORY_ROOT / project_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = _get_lock(project_id)
        self._ensure_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_entries(self, category: str, entries: List[MemoryEntry]) -> None:
        """Append entries to a category, enforcing max-entries limit."""
        if category not in CATEGORIES:
            logger.warning("Unknown memory category: %s", category)
            return
        async with self._lock:
            existing = self._load_category(category)
            existing.extend(entries)
            max_entries = settings.MEMORY_MAX_ENTRIES_PER_CATEGORY
            if len(existing) > max_entries:
                existing = existing[-max_entries:]
            self._save_category(category, existing)
            self._update_index(category, len(existing))

    def get_memory_for_role(self, role: str) -> Optional[str]:
        """Return formatted memory text for a role, or None if empty."""
        cats = ROLE_MEMORY_ACCESS.get(role, [])
        if not cats:
            return None

        parts: List[str] = []
        for cat in cats:
            entries = self._load_category(cat)
            if entries:
                lines = [f"- {e.content}" for e in entries[-10:]]  # latest 10
                parts.append(f"### {cat}\n" + "\n".join(lines))

        if not parts:
            return None
        return "\n\n".join(parts)

    def get_all_entries(self, category: str) -> List[MemoryEntry]:
        return self._load_category(category)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _category_path(self, category: str) -> Path:
        return self.root / f"{category}.json"

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _ensure_index(self) -> None:
        idx = self._index_path()
        if not idx.exists():
            idx.write_text(json.dumps({
                "project_id": self.project_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "categories": {},
            }, ensure_ascii=False, indent=2))

    def _update_index(self, category: str, count: int) -> None:
        idx_path = self._index_path()
        try:
            data = json.loads(idx_path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            data = {"project_id": self.project_id, "categories": {}}
        data["categories"][category] = {
            "count": count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        idx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _load_category(self, category: str) -> List[MemoryEntry]:
        path = self._category_path(category)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            return [MemoryEntry(**item) for item in raw]
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.warning("Corrupt memory file %s, resetting", path)
            return []

    def _save_category(self, category: str, entries: List[MemoryEntry]) -> None:
        path = self._category_path(category)
        data = [asdict(e) for e in entries]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
