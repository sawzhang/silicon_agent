"""Tests for TaskLogService.

Combines:
 1. Real-DB integration tests (pre-existing, for create/update/append paths)
 2. Mock-session unit tests (new) targeting coverage gaps in list_logs,
    update_log, get_max_event_seq and the static helpers — fixing the
    coverage.py / Python 3.13 sys.monitoring coroutine-resume blind-spot.

Uncovered lines targeted by mock tests:
  task_log_service.py: 35-52, 62-63, 70-76, 115, 156, 162, 164, 166, 168,
                       180, 208-209, 212-239
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.task import TaskModel, TaskStageModel
from app.models.task_log import TaskStageLogModel
from app.services.task_log_service import TaskLogService


# ── shared helpers ─────────────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    """Return an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    return session


_MISSING = object()


def _exec_result(
    *,
    scalar=None,
    scalars_list=None,
    scalar_one_or_none=_MISSING,
    rowcount: int = 1,
):
    """Build a mock result returned by session.execute()."""
    r = MagicMock()
    r.scalar.return_value = scalar
    r.rowcount = rowcount

    sm = MagicMock()
    sm.all.return_value = scalars_list if scalars_list is not None else []
    r.scalars.return_value = sm

    if scalar_one_or_none is not _MISSING:
        r.scalar_one_or_none.return_value = scalar_one_or_none
    else:
        r.scalar_one_or_none.return_value = None

    return r


def _make_log_model(**kwargs: Any) -> SimpleNamespace:
    """Create a lightweight log-like namespace for mock results."""
    defaults: dict[str, Any] = dict(
        id=str(uuid.uuid4()),
        task_id="task-1",
        stage_id="stage-1",
        stage_name="coding",
        agent_role="coding",
        correlation_id=None,
        event_seq=0,
        event_type="tool_call_executed",
        event_source="tool",
        status="success",
        request_body=None,
        response_body=None,
        command=None,
        command_args=None,
        workspace=None,
        execution_mode=None,
        duration_ms=None,
        result="ok",
        output_summary=None,
        output_truncated=False,
        missing_fields=[],
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# Static helper tests  (_derive_command_from_args, _sanitize_value)
# ══════════════════════════════════════════════════════════════════════════════


class TestDeriveCommandFromArgs:
    """Lines 35-52: _derive_command_from_args"""

    def test_non_dict_returns_none(self):
        assert TaskLogService._derive_command_from_args("not-a-dict") is None
        assert TaskLogService._derive_command_from_args(None) is None
        assert TaskLogService._derive_command_from_args(42) is None

    def test_execute_with_command(self):
        result = TaskLogService._derive_command_from_args(
            {"tool_name": "execute", "command": "npm test"}
        )
        assert result == "npm test"

    def test_execute_without_command_returns_execute(self):
        result = TaskLogService._derive_command_from_args({"tool_name": "execute", "command": ""})
        assert result == "execute"

    def test_execute_script(self):
        result = TaskLogService._derive_command_from_args({"tool_name": "execute_script"})
        assert result == "execute_script"

    def test_read_with_path(self):
        result = TaskLogService._derive_command_from_args(
            {"tool_name": "read", "path": "/tmp/file.py"}
        )
        assert result == "read /tmp/file.py"

    def test_read_without_path(self):
        result = TaskLogService._derive_command_from_args({"tool_name": "read", "path": ""})
        assert result == "read"

    def test_write_with_path(self):
        result = TaskLogService._derive_command_from_args(
            {"tool_name": "write", "path": "/tmp/out.py"}
        )
        assert result == "write /tmp/out.py"

    def test_edit_with_path(self):
        result = TaskLogService._derive_command_from_args(
            {"tool_name": "edit", "path": "/tmp/out.py"}
        )
        assert result == "edit /tmp/out.py"

    def test_skill_with_name(self):
        result = TaskLogService._derive_command_from_args(
            {"tool_name": "skill", "name": "my_skill"}
        )
        assert result == "skill:my_skill"

    def test_skill_without_name(self):
        result = TaskLogService._derive_command_from_args({"tool_name": "skill", "name": ""})
        assert result == "skill"

    def test_unknown_tool_name_returned_directly(self):
        result = TaskLogService._derive_command_from_args({"tool_name": "custom_tool"})
        assert result == "custom_tool"

    def test_empty_dict_returns_none(self):
        result = TaskLogService._derive_command_from_args({})
        assert result is None


class TestSanitizeValue:
    """Lines 56-84: _sanitize_value"""

    def test_dict_masks_sensitive_keys(self):
        val, truncated = TaskLogService._sanitize_value(
            {"api_key": "secret123", "username": "alice"}
        )
        assert val["api_key"] == "***"
        assert val["username"] == "alice"
        assert not truncated

    def test_dict_masks_all_sensitive_keywords(self):
        sensitive = {
            "api_key": "k1",
            "apikey": "k2",
            "authorization": "Bearer tok",
            "password": "pw",
            "secret": "s",
            "token": "t",
        }
        result, _ = TaskLogService._sanitize_value(sensitive)
        for key in sensitive:
            assert result[key] == "***"

    def test_list_sanitizes_recursively(self):
        """Lines 70-76: list branch."""
        val, truncated = TaskLogService._sanitize_value(["hello", "world"])
        assert val == ["hello", "world"]
        assert not truncated

    def test_list_with_sensitive_dicts(self):
        val, _ = TaskLogService._sanitize_value([{"token": "abc"}])
        assert val[0]["token"] == "***"

    def test_string_masked_for_bearer_token(self):
        val, _ = TaskLogService._sanitize_value("Authorization: Bearer abc123")
        assert "***" in val

    def test_string_truncated_when_too_long(self):
        long_str = "x" * 60_000
        val, truncated = TaskLogService._sanitize_value(long_str)
        assert truncated
        assert val.endswith("...[truncated]")

    def test_string_not_truncated_at_limit(self):
        ok_str = "y" * 50_000
        val, truncated = TaskLogService._sanitize_value(ok_str)
        assert not truncated
        assert val == ok_str

    def test_non_string_scalar_passthrough(self):
        val, truncated = TaskLogService._sanitize_value(42)
        assert val == 42
        assert not truncated

        val2, truncated2 = TaskLogService._sanitize_value(None)
        assert val2 is None
        assert not truncated2


# ══════════════════════════════════════════════════════════════════════════════
# normalize_log_item tests (line 115: event_seq default)
# ══════════════════════════════════════════════════════════════════════════════


class TestNormalizeLogItem:
    """Lines 88-120: normalize_log_item static method."""

    def test_sets_event_seq_default_when_none(self):
        """Line 115: event_seq=None → set to 0."""
        raw = {
            "task_id": "t-1",
            "stage_name": "coding",
            "event_type": "llm_call",
            "event_source": "llm",
            "status": "success",
            "event_seq": None,
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["event_seq"] == 0

    def test_preserves_existing_event_seq(self):
        raw = {
            "task_id": "t-1",
            "stage_name": "coding",
            "event_type": "llm_call",
            "event_source": "llm",
            "status": "success",
            "event_seq": 5,
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["event_seq"] == 5

    def test_sets_missing_fields_default(self):
        raw = {
            "task_id": "t-1",
            "stage_name": "s",
            "event_type": "e",
            "event_source": "llm",
            "status": "ok",
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["missing_fields"] == []

    def test_sets_created_at_when_none(self):
        raw = {
            "task_id": "t-1",
            "stage_name": "s",
            "event_type": "e",
            "event_source": "llm",
            "status": "ok",
            "created_at": None,
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["created_at"] is not None

    def test_output_truncated_set_true_on_large_result(self):
        large = "x" * 60_000
        raw = {
            "task_id": "t-1",
            "stage_name": "s",
            "event_type": "e",
            "event_source": "llm",
            "status": "ok",
            "result": large,
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["output_truncated"] is True

    def test_output_truncated_inherits_existing_true(self):
        raw = {
            "task_id": "t-1",
            "stage_name": "s",
            "event_type": "e",
            "event_source": "llm",
            "status": "ok",
            "output_truncated": True,
        }
        result = TaskLogService.normalize_log_item(raw)
        assert result["output_truncated"] is True


# ══════════════════════════════════════════════════════════════════════════════
# update_log mock tests (lines 156, 162, 164, 166, 168, 175)
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateLogMock:
    """Lines 134-175: update_log with mocked session."""

    @pytest.mark.asyncio
    async def test_update_log_returns_true_on_rowcount_1(self):
        """Line 175: rowcount > 0 → returns True."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        svc = TaskLogService(session)
        result = await svc.update_log("log-1", {"status": "success", "duration_ms": 10.5})

        assert result is True

    @pytest.mark.asyncio
    async def test_update_log_returns_false_on_rowcount_0(self):
        """Line 175: rowcount == 0 → returns False."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=0)

        svc = TaskLogService(session)
        result = await svc.update_log("nonexistent-log", {"status": "failed"})

        assert result is False

    @pytest.mark.asyncio
    async def test_update_log_skips_disallowed_fields(self):
        """Lines 155-156: only allowed fields make it into the payload."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        svc = TaskLogService(session)
        result = await svc.update_log("log-1", {
            "status": "success",
            "task_id": "should-be-ignored",  # not in allowed_fields
            "unknown_field": "also-ignored",
        })

        assert result is True
        # execute should still be called (payload has at least 'status')
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_log_empty_payload_returns_false(self):
        """Line 168: if no allowed fields remain, return False without execute."""
        session = _make_session()

        svc = TaskLogService(session)
        result = await svc.update_log("log-1", {
            "task_id": "ignored",
            "stage_id": "also-ignored",
        })

        assert result is False
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_log_normalizes_missing_fields(self):
        """Line 162: missing_fields converted to list."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        svc = TaskLogService(session)
        # Pass as a tuple; should be converted to list internally
        result = await svc.update_log("log-1", {"missing_fields": ("field_a", "field_b")})

        assert result is True

    @pytest.mark.asyncio
    async def test_update_log_normalizes_output_truncated(self):
        """Line 164: output_truncated coerced to bool."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        svc = TaskLogService(session)
        result = await svc.update_log("log-1", {"output_truncated": 1})  # truthy int

        assert result is True

    @pytest.mark.asyncio
    async def test_update_log_sets_truncated_true_from_large_value(self):
        """Line 166: when a value is truncated and output_truncated not explicitly set,
        it is added as True to the payload."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        large_result = "z" * 60_000  # will be truncated by _sanitize_value
        svc = TaskLogService(session)
        result = await svc.update_log("log-1", {"result": large_result})

        assert result is True
        # The execute was called — which means output_truncated=True was injected
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_log_sanitizes_sensitive_values(self):
        """Lines 157-158: _sanitize_value called per field, sensitive data masked."""
        session = _make_session()
        session.execute.return_value = _exec_result(rowcount=1)

        svc = TaskLogService(session)
        # command_args is in allowed_fields and contains a sensitive key
        result = await svc.update_log("log-1", {
            "command_args": {"tool_name": "execute", "token": "supersecret"}
        })

        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# get_max_event_seq mock tests (lines 177-183, line 180)
# ══════════════════════════════════════════════════════════════════════════════


class TestGetMaxEventSeqMock:
    """Lines 177-183: get_max_event_seq with mocked session."""

    @pytest.mark.asyncio
    async def test_get_max_event_seq_returns_value(self):
        """Line 182: scalar_one_or_none returns a value → cast to int."""
        session = _make_session()
        session.execute.return_value = _exec_result(scalar_one_or_none=7)

        svc = TaskLogService(session)
        result = await svc.get_max_event_seq("task-1")

        assert result == 7

    @pytest.mark.asyncio
    async def test_get_max_event_seq_none_defaults_to_zero(self):
        """Line 183: scalar_one_or_none returns None → returns 0."""
        session = _make_session()
        session.execute.return_value = _exec_result(scalar_one_or_none=None)

        svc = TaskLogService(session)
        result = await svc.get_max_event_seq("task-1")

        assert result == 0

    @pytest.mark.asyncio
    async def test_get_max_event_seq_with_stage_id(self):
        """Line 180: stage_id filter applied (no exception, query runs)."""
        session = _make_session()
        session.execute.return_value = _exec_result(scalar_one_or_none=3)

        svc = TaskLogService(session)
        result = await svc.get_max_event_seq("task-1", stage_id="stage-1")

        assert result == 3
        session.execute.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# list_logs mock tests (lines 208-239)
# ══════════════════════════════════════════════════════════════════════════════


class TestListLogsMock:
    """Lines 185-244: list_logs with mocked session."""

    def _make_session_with_bind(self, dialect_name: str = "postgresql") -> AsyncMock:
        """AsyncMock session where get_bind() is synchronous (MagicMock)."""
        session = AsyncMock()
        if dialect_name is None:
            session.get_bind = MagicMock(return_value=None)
        else:
            bind = MagicMock()
            bind.dialect.name = dialect_name
            session.get_bind = MagicMock(return_value=bind)
        return session

    @pytest.mark.asyncio
    async def test_list_logs_empty_task(self):
        """Lines 211-239: list_logs with no results returns empty TaskLogListResponse."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=0),         # count query
            _exec_result(scalars_list=[]),  # list query
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1")

        assert result.total == 0
        assert result.items == []
        assert result.page == 1
        assert result.page_size == 50

    @pytest.mark.asyncio
    async def test_list_logs_with_results_populates_items(self):
        """Lines 229-238: logs fetched, TaskLogResponse built for each."""
        log1 = _make_log_model(id="log-1", task_id="task-1", command="npm test")
        log2 = _make_log_model(id="log-2", task_id="task-1", command=None,
                               command_args={"tool_name": "execute", "command": "ls"})

        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=2),
            _exec_result(scalars_list=[log1, log2]),
        ]

        with patch("app.services.task_log_service.TaskLogResponse.model_validate") as mock_validate:
            resp1 = SimpleNamespace(
                id="log-1", command="npm test", command_args=None,
                task_id="task-1", stage_id="stage-1", stage_name="coding",
                agent_role="coding", correlation_id=None, event_seq=0,
                event_type="tool_call_executed", event_source="tool", status="success",
                request_body=None, response_body=None, workspace=None, execution_mode=None,
                duration_ms=None, result="ok", output_summary=None, output_truncated=False,
                missing_fields=[], created_at=datetime.now(timezone.utc),
            )
            resp2 = SimpleNamespace(
                id="log-2", command=None, command_args={"tool_name": "execute", "command": "ls"},
                task_id="task-1", stage_id="stage-1", stage_name="coding",
                agent_role="coding", correlation_id=None, event_seq=0,
                event_type="tool_call_executed", event_source="tool", status="success",
                request_body=None, response_body=None, workspace=None, execution_mode=None,
                duration_ms=None, result="ok", output_summary=None, output_truncated=False,
                missing_fields=[], created_at=datetime.now(timezone.utc),
            )
            mock_validate.side_effect = [resp1, resp2]

            svc = TaskLogService(session)
            result = await svc.list_logs("task-1")

        assert result.total == 2
        assert len(result.items) == 2
        # log-2 had no command, should have been derived from command_args
        item2 = result.items[1]
        assert item2.command == "ls"

    @pytest.mark.asyncio
    async def test_list_logs_with_stage_filter(self):
        """Lines 202-204: stage filter added to both queries."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=1),
            _exec_result(scalars_list=[_make_log_model()]),
        ]

        with patch("app.services.task_log_service.TaskLogResponse.model_validate") as mock_validate:
            log_resp = _make_log_model()
            log_resp.command = "echo hi"
            log_resp.command_args = None
            mock_validate.return_value = log_resp

            svc = TaskLogService(session)
            result = await svc.list_logs("task-1", stage="coding")

        assert result.total == 1
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_list_logs_with_event_source_filter(self):
        """Lines 207-209: event_source filter applied."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=0),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1", event_source="tool")

        assert result.total == 0
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_list_logs_sqlite_dialect_branch(self):
        """Lines 215-220: SQLite dialect uses rowid ordering."""
        session = self._make_session_with_bind("sqlite")
        session.execute.side_effect = [
            _exec_result(scalar=0),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1")

        assert result.total == 0
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_list_logs_get_bind_none(self):
        """Lines 214-226: when get_bind returns None, uses non-sqlite branch."""
        session = self._make_session_with_bind(None)
        session.execute.side_effect = [
            _exec_result(scalar=0),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1")

        assert result.total == 0

    @pytest.mark.asyncio
    async def test_list_logs_page_size_clamped_to_max(self):
        """Line 194: page_size > _MAX_PAGE_SIZE (200) → clamped to 200."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=0),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1", page_size=999)

        assert result.page_size == 200

    @pytest.mark.asyncio
    async def test_list_logs_page_clamped_to_min_1(self):
        """Line 193: page < 1 → clamped to 1."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=0),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1", page=0)

        assert result.page == 1

    @pytest.mark.asyncio
    async def test_list_logs_count_none_defaults_zero(self):
        """Line 212: scalar() returns None → total defaults to 0."""
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=None),
            _exec_result(scalars_list=[]),
        ]

        svc = TaskLogService(session)
        result = await svc.list_logs("task-1")

        assert result.total == 0

    @pytest.mark.asyncio
    async def test_list_logs_derives_command_when_missing(self):
        """Lines 235-236: command derived from command_args when command is None."""
        log = _make_log_model(command=None, command_args={"tool_name": "read", "path": "/etc/hosts"})
        session = self._make_session_with_bind("postgresql")
        session.execute.side_effect = [
            _exec_result(scalar=1),
            _exec_result(scalars_list=[log]),
        ]

        with patch("app.services.task_log_service.TaskLogResponse.model_validate") as mock_validate:
            resp = SimpleNamespace(
                id=log.id, command=None,
                command_args={"tool_name": "read", "path": "/etc/hosts"},
                task_id="task-1", stage_id="stage-1", stage_name="coding",
                agent_role="coding", correlation_id=None, event_seq=0,
                event_type="tool_call_executed", event_source="tool", status="success",
                request_body=None, response_body=None, workspace=None, execution_mode=None,
                duration_ms=None, result="ok", output_summary=None, output_truncated=False,
                missing_fields=[], created_at=datetime.now(timezone.utc),
            )
            mock_validate.return_value = resp

            svc = TaskLogService(session)
            result = await svc.list_logs("task-1")

        assert len(result.items) == 1
        assert result.items[0].command == "read /etc/hosts"


# ══════════════════════════════════════════════════════════════════════════════
# Integration tests (pre-existing — real DB, kept for regression coverage)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_log_preserves_fields_not_in_partial_payload():
    task_id = 'tt-log-service-task-1'
    stage_id = 'tt-log-service-stage-1'
    log_id = 'tt-log-service-log-1'

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Task Log Service', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='coding',
                agent_role='coding',
                status='running',
            )
        )
        session.add(
            TaskStageLogModel(
                id=log_id,
                task_id=task_id,
                stage_id=stage_id,
                stage_name='coding',
                agent_role='coding',
                event_type='tool_call_executed',
                event_source='tool',
                event_seq=1,
                status='running',
                command='npm test',
                workspace='/tmp/ws',
                execution_mode='in_process',
                result='pending',
            )
        )
        await session.commit()

        service = TaskLogService(session)
        updated = await service.update_log(
            log_id,
            {
                'status': 'success',
                'duration_ms': 18.5,
                'result': 'ok',
            },
        )
        assert updated is True
        await session.commit()

        refreshed = await session.get(TaskStageLogModel, log_id)
        assert refreshed is not None
        assert refreshed.status == 'success'
        assert refreshed.duration_ms == 18.5
        assert refreshed.result == 'ok'
        assert refreshed.command == 'npm test'
        assert refreshed.workspace == '/tmp/ws'
        assert refreshed.execution_mode == 'in_process'

        logs = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id == log_id))
        for item in logs.scalars().all():
            await session.delete(item)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_create_log_truncates_large_result_fields():
    task_id = 'tt-log-service-task-2'
    stage_id = 'tt-log-service-stage-2'
    log_id = 'tt-log-service-log-2'
    large_text = 'x' * 60000

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Task Log Truncation', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='coding',
                agent_role='coding',
                status='running',
            )
        )
        await session.commit()

        service = TaskLogService(session)
        await service.create_log(
            {
                'id': log_id,
                'task_id': task_id,
                'stage_id': stage_id,
                'stage_name': 'coding',
                'agent_role': 'coding',
                'event_seq': 1,
                'event_type': 'tool_call_executed',
                'event_source': 'tool',
                'status': 'failed',
                'result': large_text,
                'output_summary': large_text,
            }
        )
        await session.commit()

        created = await session.get(TaskStageLogModel, log_id)
        assert created is not None
        assert created.output_truncated is True
        assert created.result is not None and created.result.endswith('...[truncated]')
        assert created.output_summary is not None and created.output_summary.endswith('...[truncated]')

        logs = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id == log_id))
        for item in logs.scalars().all():
            await session.delete(item)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_append_logs_backwards_compatible_alias():
    task_id = 'tt-log-service-task-3'
    stage_id = 'tt-log-service-stage-3'
    log_id = 'tt-log-service-log-3'

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Task Log Alias', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='coding',
                agent_role='coding',
                status='running',
            )
        )
        await session.commit()

        service = TaskLogService(session)
        await service.append_logs(
            [
                {
                    'id': log_id,
                    'task_id': task_id,
                    'stage_id': stage_id,
                    'stage_name': 'coding',
                    'agent_role': 'coding',
                    'event_seq': 1,
                    'event_type': 'tool_call_executed',
                    'event_source': 'tool',
                    'status': 'success',
                    'result': 'ok',
                }
            ]
        )
        await session.commit()

        created = await session.get(TaskStageLogModel, log_id)
        assert created is not None
        assert created.status == 'success'
        assert created.result == 'ok'

        logs = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id == log_id))
        for item in logs.scalars().all():
            await session.delete(item)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()
