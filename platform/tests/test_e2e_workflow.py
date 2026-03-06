from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e.yml"


def test_control_plane_regression_step_enables_pipefail() -> None:
    content = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"- name: Run deterministic control-plane regression\n(?P<body>(?:\s+.*\n)+?)(?:\s+- name:|\Z)",
        content,
    )

    assert match is not None
    body = match.group("body")
    assert "shell: bash" in body
    assert "set -o pipefail" in body
