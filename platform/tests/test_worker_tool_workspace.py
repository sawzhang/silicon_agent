from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.worker.agents import SandboxedAgentRunner
from app.worker.executor import infer_tool_status


def _make_runner(workspace: Path) -> SandboxedAgentRunner:
    runner = object.__new__(SandboxedAgentRunner)
    runner.default_cwd = str(workspace)
    runner.allowed_tools = {"read", "write", "execute", "execute_script", "skill"}
    return runner


def test_resolve_workspace_path_for_relative_read_write(tmp_path: Path):
    runner = _make_runner(tmp_path)

    resolved, error = runner._resolve_workspace_path("src/App.jsx")
    assert error is None
    assert resolved == str((tmp_path / "src" / "App.jsx").resolve())

    abs_path = str((tmp_path / "README.md").resolve())
    resolved_abs, error_abs = runner._resolve_workspace_path(abs_path)
    assert error_abs is None
    assert resolved_abs == abs_path


def test_resolve_workspace_path_blocks_escape(tmp_path: Path):
    runner = _make_runner(tmp_path)
    _, error = runner._resolve_workspace_path("../outside.txt")
    assert error is not None
    assert "escapes workspace" in error


@pytest.mark.asyncio
async def test_read_directory_returns_listing(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "api.md").write_text("api", encoding="utf-8")
    (docs / "usage.md").write_text("usage", encoding="utf-8")

    runner = _make_runner(tmp_path)
    result = await runner._execute_tool(
        {"name": "read", "arguments": json.dumps({"path": "docs"})}
    )

    assert "Directory listing for docs:" in result
    assert "- api.md" in result
    assert "- usage.md" in result


@pytest.mark.asyncio
async def test_invalid_tool_arguments_returns_error(tmp_path: Path):
    runner = _make_runner(tmp_path)
    result = await runner._execute_tool(
        {"name": "write", "arguments": json.dumps([{"path": "a.txt", "content": "x"}])}
    )
    assert "Invalid arguments for tool write" in result
    assert "expected JSON object" in result


def test_infer_tool_status_treats_read_errors_as_failed():
    assert infer_tool_status("Error reading file: [Errno 21] Is a directory") == "failed"
    assert infer_tool_status("Error: File not found: package.json") == "failed"
    assert infer_tool_status("Error (exit 1): command failed") == "failed"
    assert infer_tool_status("normal output") == "success"
