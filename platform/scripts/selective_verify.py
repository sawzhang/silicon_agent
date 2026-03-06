from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from scripts.verify_harness import TARGETS, _run_target
except ModuleNotFoundError:  # pragma: no cover - CLI entrypoint path
    from verify_harness import TARGETS, _run_target


ROOT = Path(__file__).resolve().parents[2]
TARGET_PRIORITY = ["contracts", "worker", "api-core", "frontend-logs", "frontend", "core"]
IGNORED_PREFIXES = (
    "platform/.venv/",
    "web/coverage/",
    "web/dist/",
    "web/node_modules/",
)
CORE_FILES = {
    "Makefile",
    "platform/scripts/verify_harness.py",
    "platform/scripts/selective_verify.py",
    "platform/tests/test_verify_harness.py",
    "platform/tests/test_selective_verify.py",
}
CONTRACT_EXACT = {
    "platform/app/worker/prompts.py",
    "platform/app/services/template_service.py",
    "platform/tests/test_prompts.py",
    "platform/tests/test_template_contracts.py",
}
WORKER_PREFIXES = (
    "platform/app/worker/",
)
WORKER_EXACT = {
    "platform/tests/test_executor_stage_logs.py",
    "platform/tests/test_engine_stage_execution.py",
    "platform/tests/test_worker_graph.py",
}
API_CORE_PREFIXES = (
    "platform/app/api/v1/",
)
API_CORE_EXACT = {
    "platform/app/services/task_service.py",
    "platform/app/services/gate_service.py",
    "platform/app/schemas/task.py",
    "platform/app/schemas/gate.py",
    "platform/tests/test_tasks_api.py",
    "platform/tests/test_gates_api.py",
    "platform/tests/test_task_logs_api.py",
}
FRONTEND_LOGS_PREFIXES = (
    "web/src/pages/TaskLogs/",
)
FRONTEND_LOGS_EXACT = {
    "web/src/hooks/useWebSocket.ts",
    "web/src/hooks/useWebSocket.test.ts",
    "web/src/stores/taskLogStreamStore.ts",
    "web/src/stores/taskLogStreamStore.test.ts",
    "web/src/services/taskLogApi.ts",
}
FRONTEND_PREFIXES = (
    "web/",
)


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _matches(path: str, exact: set[str], prefixes: tuple[str, ...]) -> bool:
    return path in exact or any(path.startswith(prefix) for prefix in prefixes)


def _target_for_path(path: str) -> str | None:
    if _matches(path, CORE_FILES, ()):
        return "core"
    if _matches(path, CONTRACT_EXACT, ()):
        return "contracts"
    if _matches(path, FRONTEND_LOGS_EXACT, FRONTEND_LOGS_PREFIXES):
        return "frontend-logs"
    if _matches(path, API_CORE_EXACT, API_CORE_PREFIXES):
        return "api-core"
    if _matches(path, WORKER_EXACT, WORKER_PREFIXES):
        return "worker"
    if _matches(path, set(), FRONTEND_PREFIXES):
        return "frontend"
    return None


def select_targets(paths: list[str]) -> list[str]:
    if not paths:
        return ["core"]

    normalized = [_normalize_path(path) for path in paths]
    selected = {
        target
        for path in normalized
        if not any(path.startswith(prefix) for prefix in IGNORED_PREFIXES)
        for target in [_target_for_path(path)]
        if target is not None
    }

    if not selected:
        return ["core"]
    if "core" in selected:
        return ["core"]
    if "frontend" in selected:
        selected.discard("frontend-logs")

    return [target for target in TARGET_PRIORITY if target in selected]


def _discover_changed_files() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "Failed to inspect git status")

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def _render_payload(paths: list[str], targets: list[str], output_format: str) -> str:
    payload = {"paths": paths, "targets": targets}
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)

    lines = ["[selective-verify]"]
    for target in targets:
        lines.append(f"- {target}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Map changed files to the smallest useful verification targets.")
    parser.add_argument("files", nargs="*", help="changed files to inspect; defaults to git status output")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--run", action="store_true", help="execute the selected verification targets")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    files = [_normalize_path(path) for path in args.files] if args.files else _discover_changed_files()
    targets = select_targets(files)

    if args.run:
        for target in targets:
            exit_code = _run_target(TARGETS[target])
            if exit_code != 0:
                return exit_code
        return 0

    print(_render_payload(files, targets, args.format))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code not in (0, None) and isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            raise SystemExit(2) from None
        raise
