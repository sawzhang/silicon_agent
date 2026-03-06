from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "platform" / "scripts" / "e2e_control_plane.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("e2e_control_plane", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_control_plane_flow_uses_gate_capable_template_and_scoped_gate_query() -> None:
    module = _load_module()
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/v1/templates":
            return {
                "items": [
                    {
                        "id": "tpl-quick",
                        "name": "quick_fix",
                        "stages": [
                            {"name": "parse"},
                            {"name": "code"},
                            {"name": "test"},
                            {"name": "signoff"},
                        ],
                        "gates": [],
                    },
                    {
                        "id": "tpl-full",
                        "name": "full_pipeline",
                        "stages": [
                            {"name": "parse"},
                            {"name": "spec"},
                            {"name": "code"},
                            {"name": "test"},
                            {"name": "signoff"},
                        ],
                        "gates": [{"after_stage": "spec", "type": "human_approve"}],
                    },
                ]
            }
        if path == "/api/v1/tasks":
            assert payload is not None
            assert payload["template_id"] == "tpl-full"
            return {"id": "task-1"}
        if path == "/api/v1/tasks/task-1/stages":
            return [
                {"stage_name": "parse"},
                {"stage_name": "spec"},
                {"stage_name": "code"},
                {"stage_name": "test"},
                {"stage_name": "signoff"},
            ]
        if path == "/api/v1/task-logs?task=task-1":
            return {"items": [], "total": 0}
        if path == "/api/v1/gates?status=pending&task_id=task-1":
            return {"items": [{"id": "gate-1", "status": "pending"}]}
        if path == "/api/v1/gates/gate-1/approve":
            return {"id": "gate-1", "status": "approved"}
        raise AssertionError(f"Unexpected request: {method} {path}")

    result = module.run_control_plane_flow(fake_request)

    assert result["task_id"] == "task-1"
    assert result["approved_gate_id"] == "gate-1"
    assert [path for _method, path, _payload in calls] == [
        "/health",
        "/api/v1/templates",
        "/api/v1/tasks",
        "/api/v1/tasks/task-1/stages",
        "/api/v1/task-logs?task=task-1",
        "/api/v1/gates?status=pending&task_id=task-1",
        "/api/v1/gates/gate-1/approve",
    ]


def test_run_control_plane_flow_requires_gate_capable_template() -> None:
    module = _load_module()

    def fake_request(method: str, path: str, payload: dict | None = None):
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/v1/templates":
            return {
                "items": [
                    {
                        "id": "tpl-quick",
                        "name": "quick_fix",
                        "stages": [
                            {"name": "parse"},
                            {"name": "code"},
                            {"name": "test"},
                            {"name": "signoff"},
                        ],
                        "gates": [],
                    }
                ]
            }
        raise AssertionError(f"Unexpected request: {method} {path}")

    with pytest.raises(module.ControlPlaneCheckError, match="gate-capable"):
        module.run_control_plane_flow(fake_request)


def test_run_control_plane_flow_requires_expected_stage_shape() -> None:
    module = _load_module()

    def fake_request(method: str, path: str, payload: dict | None = None):
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/v1/templates":
            return {
                "items": [
                    {
                        "id": "tpl-full",
                        "name": "full_pipeline",
                        "stages": [
                            {"name": "parse"},
                            {"name": "spec"},
                            {"name": "code"},
                            {"name": "test"},
                            {"name": "signoff"},
                        ],
                        "gates": [{"after_stage": "spec", "type": "human_approve"}],
                    }
                ]
            }
        if path == "/api/v1/tasks":
            return {"id": "task-1"}
        if path == "/api/v1/tasks/task-1/stages":
            return [
                {"stage_name": "parse"},
                {"stage_name": "code"},
                {"stage_name": "signoff"},
            ]
        raise AssertionError(f"Unexpected request: {method} {path}")

    with pytest.raises(module.ControlPlaneCheckError, match="test"):
        module.run_control_plane_flow(fake_request)
