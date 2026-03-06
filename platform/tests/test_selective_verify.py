from __future__ import annotations

from scripts.selective_verify import select_targets


def test_select_targets_defaults_to_core_when_no_files() -> None:
    assert select_targets([]) == ["core"]


def test_select_targets_maps_prompt_and_template_paths_to_contracts() -> None:
    targets = select_targets(
        [
            "platform/app/worker/prompts.py",
            "platform/app/services/template_service.py",
        ]
    )

    assert targets == ["contracts"]


def test_select_targets_maps_worker_and_api_files_to_distinct_targets() -> None:
    targets = select_targets(
        [
            "platform/app/worker/executor.py",
            "platform/app/api/v1/task_logs.py",
        ]
    )

    assert targets == ["worker", "api-core"]


def test_select_targets_prefers_frontend_over_frontend_logs_when_both_match() -> None:
    targets = select_targets(
        [
            "web/src/hooks/useWebSocket.ts",
            "web/src/pages/Tasks/index.tsx",
        ]
    )

    assert targets == ["frontend"]


def test_select_targets_elevates_harness_changes_to_core() -> None:
    targets = select_targets(["Makefile"])

    assert targets == ["core"]
