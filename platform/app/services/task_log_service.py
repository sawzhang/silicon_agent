from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Optional

from sqlalchemy import func, literal_column, select, update
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
_MAX_TEXT_LEN = 50_000
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
    def _sanitize_value(value: Any) -> tuple[Any, bool]:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            truncated = False
            for key, item in value.items():
                key_lower = key.lower()
                if any(k in key_lower for k in _SENSITIVE_KEYWORDS):
                    sanitized[key] = "***"
                    continue
                sanitized_item, item_truncated = TaskLogService._sanitize_value(item)
                truncated = truncated or item_truncated
                sanitized[key] = sanitized_item
            return sanitized, truncated

        if isinstance(value, list):
            items: list[Any] = []
            truncated = False
            for item in value:
                sanitized_item, item_truncated = TaskLogService._sanitize_value(item)
                truncated = truncated or item_truncated
                items.append(sanitized_item)
            return items, truncated

        if isinstance(value, str):
            masked = _TOKEN_RE.sub("***", value)
            if len(masked) > _MAX_TEXT_LEN:
                return masked[:_MAX_TEXT_LEN] + "\n...[truncated]", True
            return masked, False

        return value, False

    @classmethod
    def normalize_log_item(cls, raw: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw)

        request_body, request_truncated = cls._sanitize_value(item.get("request_body"))
        response_body, response_truncated = cls._sanitize_value(item.get("response_body"))
        command_args, command_args_truncated = cls._sanitize_value(item.get("command_args"))
        result, result_truncated = cls._sanitize_value(item.get("result"))
        output_summary, summary_truncated = cls._sanitize_value(item.get("output_summary"))

        item["request_body"] = request_body
        item["response_body"] = response_body
        item["command_args"] = command_args
        item["result"] = result
        item["output_summary"] = output_summary

        item["output_truncated"] = bool(
            item.get("output_truncated")
            or request_truncated
            or response_truncated
            or command_args_truncated
            or result_truncated
            or summary_truncated
        )

        if item.get("created_at") is None:
            item["created_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

        if item.get("event_seq") is None:
            item["event_seq"] = 0

        if item.get("missing_fields") is None:
            item["missing_fields"] = []

        return item

    async def create_log(self, raw: dict[str, Any]) -> None:
        item = self.normalize_log_item(raw)
        self.session.add(TaskStageLogModel(**item))

    async def create_logs(self, logs: list[dict[str, Any]]) -> None:
        for raw in logs:
            await self.create_log(raw)

    async def update_log(self, log_id: str, updates: dict[str, Any]) -> bool:
        allowed_fields = {
            "event_type",
            "event_source",
            "status",
            "request_body",
            "response_body",
            "command",
            "command_args",
            "workspace",
            "duration_ms",
            "result",
            "output_summary",
            "output_truncated",
            "missing_fields",
            "correlation_id",
        }
        payload: dict[str, Any] = {}
        truncated = False
        for key, value in updates.items():
            if key not in allowed_fields:
                continue
            sanitized_value, value_truncated = self._sanitize_value(value)
            truncated = truncated or value_truncated
            payload[key] = sanitized_value

        if "missing_fields" in payload:
            payload["missing_fields"] = list(payload["missing_fields"] or [])
        if "output_truncated" in payload:
            payload["output_truncated"] = bool(payload["output_truncated"])
        elif truncated:
            payload["output_truncated"] = True
        if not payload:
            return False

        result = await self.session.execute(
            update(TaskStageLogModel)
            .where(TaskStageLogModel.id == log_id)
            .values(**payload)
        )
        return (result.rowcount or 0) > 0

    async def get_max_event_seq(self, task_id: str, stage_id: Optional[str] = None) -> int:
        query = select(func.max(TaskStageLogModel.event_seq)).where(TaskStageLogModel.task_id == task_id)
        if stage_id is not None:
            query = query.where(TaskStageLogModel.stage_id == stage_id)
        result = await self.session.execute(query)
        value = result.scalar_one_or_none()
        return int(value or 0)

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

        source_value = event_source.strip() if event_source else None
        if source_value:
            query = query.where(TaskStageLogModel.event_source == source_value)
            count_query = count_query.where(TaskStageLogModel.event_source == source_value)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            query = query.order_by(
                TaskStageLogModel.event_seq.asc(),
                TaskStageLogModel.created_at.asc(),
                literal_column(f"{TaskStageLogModel.__tablename__}.rowid").asc(),
            )
        else:
            query = query.order_by(
                TaskStageLogModel.event_seq.asc(),
                TaskStageLogModel.created_at.asc(),
                TaskStageLogModel.id.asc(),
            )
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
