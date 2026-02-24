from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Optional

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_log import TaskStageLogModel
from app.schemas.task_log import TaskLogListResponse, TaskLogResponse

_SENSITIVE_KEYWORDS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}
_MAX_TEXT_LEN = 20_000
_MAX_PAGE_SIZE = 200
_TOKEN_RE = re.compile(r"(?i)(bearer\s+[a-z0-9_\-\.]+)")


class TaskLogService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _derive_command_from_args(command_args: Any) -> Optional[str]:
        if not isinstance(command_args, dict):
            return None

        tool_name = str(command_args.get("tool_name") or "").strip()
        if tool_name == "execute":
            command = str(command_args.get("command") or "").strip()
            return command or "execute"
        if tool_name == "execute_script":
            return "execute_script"
        if tool_name == "read":
            path = str(command_args.get("path") or "").strip()
            return f"read {path}".strip()
        if tool_name == "write":
            path = str(command_args.get("path") or "").strip()
            return f"write {path}".strip()
        if tool_name == "skill":
            name = str(command_args.get("name") or "").strip()
            return f"skill:{name}" if name else "skill"
        if tool_name:
            return tool_name
        return None

    @staticmethod
    def _mask_sensitive_value(value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_lower = key.lower()
                if any(k in key_lower for k in _SENSITIVE_KEYWORDS):
                    sanitized[key] = "***"
                else:
                    sanitized[key] = TaskLogService._mask_sensitive_value(item)
            return sanitized

        if isinstance(value, list):
            return [TaskLogService._mask_sensitive_value(item) for item in value]

        if isinstance(value, str):
            masked = _TOKEN_RE.sub("***", value)
            if len(masked) > _MAX_TEXT_LEN:
                return masked[:_MAX_TEXT_LEN] + "\n...[truncated]"
            return masked

        return value

    async def append_logs(self, logs: list[dict[str, Any]]) -> None:
        for raw in logs:
            item = dict(raw)
            item["request_body"] = self._mask_sensitive_value(item.get("request_body"))
            item["response_body"] = self._mask_sensitive_value(item.get("response_body"))
            item["command_args"] = self._mask_sensitive_value(item.get("command_args"))
            item["result"] = self._mask_sensitive_value(item.get("result"))
            # Ensure newly written logs have microsecond precision for deterministic ordering.
            if item.get("created_at") is None:
                item["created_at"] = datetime.utcnow()
            self.session.add(TaskStageLogModel(**item))

    async def list_logs(
        self,
        task_id: str,
        stage: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        event_source: Optional[str] = None,
    ) -> TaskLogListResponse:
        page = max(1, page)
        page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

        query = select(TaskStageLogModel).where(TaskStageLogModel.task_id == task_id)
        count_query = select(func.count()).select_from(TaskStageLogModel).where(
            TaskStageLogModel.task_id == task_id,
        )

        stage_value = stage.strip() if stage else None
        if stage_value:
            query = query.where(TaskStageLogModel.stage_name == stage_value)
            count_query = count_query.where(TaskStageLogModel.stage_name == stage_value)

        if event_source:
            query = query.where(TaskStageLogModel.event_source == event_source)
            count_query = count_query.where(TaskStageLogModel.event_source == event_source)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            # SQLite CURRENT_TIMESTAMP is second precision, so rowid keeps insertion order
            # for records created in the same second.
            query = query.order_by(
                TaskStageLogModel.created_at.asc(),
                literal_column(f"{TaskStageLogModel.__tablename__}.rowid").asc(),
            )
        else:
            query = query.order_by(TaskStageLogModel.created_at.asc(), TaskStageLogModel.id.asc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        logs = result.scalars().all()

        items: list[TaskLogResponse] = []
        for log in logs:
            item = TaskLogResponse.model_validate(log)
            if not item.command:
                item.command = self._derive_command_from_args(item.command_args)
            items.append(item)

        return TaskLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
