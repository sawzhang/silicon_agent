from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = ROOT / "platform"
WEB_ROOT = ROOT / "web"
VENV_PYTHON = PLATFORM_ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _backend_cmd(*parts: str) -> str:
    return f"cd {PLATFORM_ROOT} && {' '.join(parts)}"


def _frontend_cmd(*parts: str) -> str:
    return f"cd {WEB_ROOT} && {' '.join(parts)}"


TARGETS: dict[str, list[dict[str, str]]] = {
    "api-core": [
        {
            "name": "api-core-tests",
            "cmd": _backend_cmd(
                PYTHON,
                "-m",
                "pytest",
                "tests/test_tasks_api.py",
                "tests/test_gates_api.py",
                "tests/test_task_logs_api.py",
                "-v",
                "--tb=short",
            ),
        },
    ],
    "contracts": [
        {
            "name": "prompt-template-contract-tests",
            "cmd": _backend_cmd(
                PYTHON,
                "-m",
                "pytest",
                "tests/test_prompts.py",
                "tests/test_template_contracts.py",
                "-v",
                "--tb=short",
            ),
        },
    ],
    "frontend": [
        {
            "name": "frontend-unit",
            "cmd": _frontend_cmd("npm", "run", "test:unit:ci"),
        },
        {
            "name": "frontend-build",
            "cmd": _frontend_cmd("npm", "run", "build"),
        },
    ],
    "frontend-logs": [
        {
            "name": "frontend-log-unit",
            "cmd": _frontend_cmd(
                "npm",
                "run",
                "test:unit:ci",
                "--",
                "src/stores/taskLogStreamStore.test.ts",
                "src/hooks/useWebSocket.test.ts",
            ),
        },
        {
            "name": "frontend-build",
            "cmd": _frontend_cmd("npm", "run", "build"),
        },
    ],
    "worker": [
        {
            "name": "backend-lint",
            "cmd": _backend_cmd(PYTHON, "-m", "ruff", "check", "app/", "tests/"),
        },
        {
            "name": "worker-tests",
            "cmd": _backend_cmd(
                PYTHON,
                "-m",
                "pytest",
                "tests/test_verify_harness.py",
                "tests/test_executor_stage_logs.py",
                "tests/test_engine_stage_execution.py",
                "tests/test_worker_graph.py",
                "-v",
                "--tb=short",
            ),
        },
    ],
}

TARGETS["core"] = [
    *TARGETS["worker"],
    *TARGETS["contracts"],
    *TARGETS["api-core"],
    *TARGETS["frontend"],
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified verification harness for this repository.")
    parser.add_argument("--target", help="verification target to print or run")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format when printing target commands",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="list supported verification targets and exit",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute the commands for the selected target",
    )
    return parser.parse_args()


def _ensure_target(name: str) -> list[dict[str, str]]:
    try:
        return TARGETS[name]
    except KeyError as exc:
        raise SystemExit(f"Unknown target: {name}") from exc


def _render_payload(target: str, commands: list[dict[str, str]], output_format: str) -> str:
    payload = {"target": target, "commands": commands}
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)

    lines = [f"[{target}]"]
    for item in commands:
        lines.append(f"- {item['name']}: {item['cmd']}")
    return "\n".join(lines)


def _run_target(commands: list[dict[str, str]]) -> int:
    for item in commands:
        print(f"==> {item['name']}")
        proc = subprocess.run(item["cmd"], shell=True, cwd=ROOT, check=False)
        if proc.returncode != 0:
            return proc.returncode
    return 0


def main() -> int:
    args = _parse_args()

    if args.list_targets:
        payload = {"targets": sorted(TARGETS)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.target:
        raise SystemExit("Either --list-targets or --target is required")

    commands = _ensure_target(args.target)
    if args.run:
        return _run_target(commands)

    print(_render_payload(args.target, commands, args.format))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code not in (0, None) and isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            raise SystemExit(2) from None
        raise
