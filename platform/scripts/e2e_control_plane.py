from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable
from urllib import error, parse, request


class ControlPlaneCheckError(RuntimeError):
    """Raised when the control-plane regression flow detects an unexpected response."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ControlPlaneCheckError(message)


def _find_gate_capable_template(payload: dict[str, Any]) -> dict[str, Any] | None:
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        gates = item.get("gates")
        if not isinstance(gates, list) or not gates:
            continue
        stages = item.get("stages")
        if not isinstance(stages, list):
            continue
        stage_names = {
            str(stage.get("name") or stage.get("stage_name") or "").strip()
            for stage in stages
            if isinstance(stage, dict)
        }
        if {"parse", "code", "test", "signoff"}.issubset(stage_names):
            return item
    return None


def _stage_names(payload: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for item in payload:
        if isinstance(item, dict):
            stage_name = item.get("stage_name")
            if isinstance(stage_name, str) and stage_name:
                names.add(stage_name)
    return names


def _pending_gate_path(task_id: str) -> str:
    return f"/api/v1/gates?{parse.urlencode({'status': 'pending', 'task_id': task_id})}"


def _wait_for_pending_gate(
    request_json: Callable[[str, str, dict[str, Any] | None], Any],
    task_id: str,
    attempts: int = 10,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    gate_path = _pending_gate_path(task_id)
    for attempt in range(attempts):
        gates = request_json("GET", gate_path, None)
        _require(isinstance(gates, dict), "gates response must be an object")
        gate_items = gates.get("items")
        if isinstance(gate_items, list) and gate_items:
            first_gate = gate_items[0]
            _require(isinstance(first_gate, dict), "pending gate item must be an object")
            return first_gate
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    raise ControlPlaneCheckError(f"no pending gate available for task {task_id}")


def run_control_plane_flow(
    request_json: Callable[[str, str, dict[str, Any] | None], Any]
) -> dict[str, Any]:
    health = request_json("GET", "/health", None)
    _require(isinstance(health, dict) and health.get("status") == "ok", "health check failed")

    templates = request_json("GET", "/api/v1/templates", None)
    _require(isinstance(templates, dict), "templates response must be an object")
    template = _find_gate_capable_template(templates)
    _require(template is not None, "gate-capable template is missing")

    created_task = request_json(
        "POST",
        "/api/v1/tasks",
        {
            "title": "E2E control-plane regression",
            "description": "Deterministic regression task for control-plane validation.",
            "template_id": template["id"],
        },
    )
    _require(isinstance(created_task, dict), "task create response must be an object")
    task_id = str(created_task.get("id") or "").strip()
    _require(task_id, "task create response is missing id")

    stages = request_json("GET", f"/api/v1/tasks/{task_id}/stages", None)
    _require(isinstance(stages, list), "task stages response must be a list")
    names = _stage_names(stages)
    for expected in ("parse", "code", "test", "signoff"):
        _require(expected in names, f"expected stage '{expected}' is missing")

    task_logs = request_json("GET", f"/api/v1/task-logs?task={task_id}", None)
    _require(isinstance(task_logs, dict), "task logs response must be an object")
    _require("items" in task_logs and "total" in task_logs, "task logs response is malformed")

    gate = _wait_for_pending_gate(request_json, task_id)
    gate_id = str(gate.get("id") or "").strip()
    _require(gate_id, "pending gate is missing id")

    approved_gate = request_json(
        "POST",
        f"/api/v1/gates/{gate_id}/approve",
        {"reviewer": "e2e-control-plane"},
    )
    _require(isinstance(approved_gate, dict), "approve gate response must be an object")
    _require(approved_gate.get("status") == "approved", "pending gate approval failed")

    return {
        "task_id": task_id,
        "approved_gate_id": gate_id,
        "stage_names": sorted(names),
        "task_log_total": task_logs.get("total", 0),
    }


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = request.Request(url, method=method, headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ControlPlaneCheckError(f"{method} {path} failed: {exc.code} {details}") from exc
    except error.URLError as exc:
        raise ControlPlaneCheckError(f"{method} {path} failed: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ControlPlaneCheckError(f"{method} {path} returned non-JSON body: {body[:200]}") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic control-plane regression checks.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="platform base URL")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = run_control_plane_flow(
        lambda method, path, payload=None: request_json(args.base_url, method, path, payload)
    )
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Control-plane regression passed")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlPlaneCheckError as exc:
        print(f"Control-plane regression failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
