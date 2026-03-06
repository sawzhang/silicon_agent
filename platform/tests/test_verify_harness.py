from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "platform" / "scripts" / "verify_harness.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_list_targets_returns_known_targets() -> None:
    result = _run_script("--list-targets")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["targets"] == [
        "api-core",
        "contracts",
        "core",
        "frontend",
        "frontend-logs",
        "worker",
    ]


def test_print_core_target_returns_named_commands() -> None:
    result = _run_script("--target", "core", "--format", "json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "core"
    command_names = [item["name"] for item in payload["commands"]]
    assert command_names == [
        "backend-lint",
        "worker-tests",
        "prompt-template-contract-tests",
        "api-core-tests",
        "frontend-unit",
        "frontend-build",
    ]
    assert "test_verify_harness.py" in payload["commands"][1]["cmd"]
    assert "test_selective_verify.py" in payload["commands"][1]["cmd"]
    assert "test_e2e_control_plane.py" in payload["commands"][1]["cmd"]


def test_print_contracts_target_returns_prompt_template_contract_commands() -> None:
    result = _run_script("--target", "contracts", "--format", "json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "contracts"
    command_names = [item["name"] for item in payload["commands"]]
    assert command_names == ["prompt-template-contract-tests"]
    assert "test_prompts.py" in payload["commands"][0]["cmd"]
    assert "test_template_contracts.py" in payload["commands"][0]["cmd"]


def test_print_worker_target_includes_boxlite_and_workflow_regressions() -> None:
    result = _run_script("--target", "worker", "--format", "json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "worker"
    worker_tests_cmd = payload["commands"][1]["cmd"]
    assert "tests/test_sandbox_boxlite.py" in worker_tests_cmd
    assert "tests/test_e2e_workflow.py" in worker_tests_cmd


def test_unknown_target_returns_non_zero_exit_code() -> None:
    result = _run_script("--target", "unknown")

    assert result.returncode == 2
    assert "Unknown target" in result.stderr
