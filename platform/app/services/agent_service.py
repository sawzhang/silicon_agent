from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integration.llm_client import get_llm_client
from app.models.agent import AgentModel
from app.schemas.agent import (
    AgentConfigUpdate,
    AgentSessionResponse,
    AgentStatusResponse,
    TokenUsage,
)

logger = logging.getLogger(__name__)

AGENT_ROLES = [
    ("orchestrator", "Orchestrator Agent"),
    ("spec", "Spec Agent"),
    ("coding", "Coding Agent"),
    ("test", "Test Agent"),
    ("review", "Review Agent"),
    ("smoke", "Smoke Test Agent"),
    ("doc", "Documentation Agent"),
]

DEFAULT_AVAILABLE_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-5-haiku-20241022",
]

THINKING_LEVELS = ["off", "low", "medium", "high"]

FALLBACK_ROLE_DEFAULT_MODELS = {
    "orchestrator": "claude-opus-4-20250514",
    "spec": "claude-opus-4-20250514",
    "coding": "claude-sonnet-4-20250514",
    "test": "claude-sonnet-4-20250514",
    "review": "claude-opus-4-20250514",
    "smoke": "claude-sonnet-4-20250514",
    "doc": "claude-sonnet-4-20250514",
}


class AgentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_agents_exist(self) -> None:
        for role, display_name in AGENT_ROLES:
            result = await self.session.execute(
                select(AgentModel).where(AgentModel.role == role)
            )
            if result.scalar_one_or_none() is None:
                agent = AgentModel(role=role, display_name=display_name, status="idle")
                self.session.add(agent)
        await self.session.commit()

    async def list_agents(self) -> List[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).order_by(AgentModel.role)
        )
        agents = result.scalars().all()
        return [AgentStatusResponse.model_validate(a) for a in agents]

    async def get_agent(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return AgentStatusResponse.model_validate(agent)

    async def update_config(
        self, role: str, update: AgentConfigUpdate
    ) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        resolved_model = update.get_model_name()
        if resolved_model is not None:
            agent.model_name = resolved_model
        current = dict(agent.config or {})
        if update.config is not None:
            current.update(update.config)

        normalized_extra_skill_dirs: Optional[list[str]] = None
        if update.extra_skill_dirs is not None:
            normalized_extra_skill_dirs = self._normalize_extra_skill_dirs(
                update.extra_skill_dirs
            )

        extra = {
            "temperature": update.temperature,
            "max_tokens": update.max_tokens,
            "max_turns": update.max_turns,
            "thinking_level": update.thinking_level,
            "extra_skill_dirs": normalized_extra_skill_dirs,
            "system_prompt_append": update.system_prompt_append,
        }
        for key, value in extra.items():
            if value is not None:
                current[key] = value

        if current:
            agent.config = current
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def get_config_options(self) -> dict:
        available_models = await self._get_available_models()
        return {
            "available_models": available_models,
            "thinking_levels": THINKING_LEVELS,
            "role_defaults": self._build_role_defaults(available_models),
        }

    async def _get_available_models(self) -> list[str]:
        if settings.LLM_API_KEY:
            try:
                models = await get_llm_client().list_models()
                if models:
                    return models
            except Exception as exc:
                logger.warning(
                    "Failed to load models from %s/v1/models: %s",
                    settings.LLM_BASE_URL.rstrip("/"),
                    exc,
                )

        candidates = [
            settings.LLM_MODEL,
            *self._parse_role_model_map().values(),
            *DEFAULT_AVAILABLE_MODELS,
        ]
        return self._dedupe_models(candidates)

    def _build_role_defaults(self, available_models: list[str]) -> dict[str, str]:
        role_model_map = self._parse_role_model_map()
        role_defaults: dict[str, str] = {}
        for role, _ in AGENT_ROLES:
            model = (
                role_model_map.get(role)
                or FALLBACK_ROLE_DEFAULT_MODELS.get(role)
                or settings.LLM_MODEL
            )
            if model not in available_models and available_models:
                if settings.LLM_MODEL in available_models:
                    model = settings.LLM_MODEL
                else:
                    model = available_models[0]
            if model:
                role_defaults[role] = model
        return role_defaults

    @staticmethod
    def _dedupe_models(models: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for model in models:
            if not isinstance(model, str):
                continue
            normalized = model.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _parse_role_model_map() -> dict[str, str]:
        raw = settings.LLM_ROLE_MODEL_MAP
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        parsed: dict[str, str] = {}
        for role, model in value.items():
            if isinstance(role, str) and isinstance(model, str) and role and model:
                parsed[role] = model
        return parsed

    @staticmethod
    def _builtin_skills_root() -> Path:
        return (Path(__file__).resolve().parents[2] / "skills").resolve()

    def _allowed_skill_dir_roots(self) -> list[Path]:
        roots: list[Path] = [self._builtin_skills_root()]
        raw = settings.EXTRA_SKILL_DIR_WHITELIST.strip()
        if not raw:
            return roots
        for item in raw.split(","):
            candidate = item.strip()
            if not candidate:
                continue
            roots.append(Path(candidate).expanduser().resolve())
        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(root)
        return deduped

    def _normalize_extra_skill_dirs(self, dirs: list[str]) -> list[str]:
        allowed_roots = self._allowed_skill_dir_roots()
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in dirs:
            value = str(raw or "").strip()
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ValueError(
                    f"extra_skill_dirs must use absolute paths: '{value}'"
                )
            resolved = path.resolve()
            if not resolved.is_dir():
                raise ValueError(
                    f"extra_skill_dirs path is not an existing directory: '{resolved}'"
                )
            if not any(
                resolved == root or root in resolved.parents
                for root in allowed_roots
            ):
                allowed = ", ".join(str(root) for root in allowed_roots)
                raise ValueError(
                    f"extra_skill_dirs path is not in whitelist: '{resolved}'. "
                    f"Allowed prefixes: {allowed}"
                )
            normalized_value = str(resolved)
            if normalized_value in seen:
                continue
            seen.add(normalized_value)
            normalized.append(normalized_value)
        return normalized

    async def mark_running(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        agent.status = "running"
        agent.started_at = datetime.now(timezone.utc)
        agent.last_active_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def mark_stopped(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        agent.status = "idle"
        agent.current_task_id = None
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def get_session(self, role: str) -> Optional[AgentSessionResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None

        uptime = None
        if agent.status == "running" and agent.started_at:
            delta = datetime.now(timezone.utc) - agent.started_at.replace(
                tzinfo=timezone.utc
            )
            uptime = delta.total_seconds()

        return AgentSessionResponse(
            role=agent.role,
            status=agent.status,
            current_task_id=agent.current_task_id,
            uptime_seconds=uptime,
            token_usage=TokenUsage(),
            turns=0,
        )
