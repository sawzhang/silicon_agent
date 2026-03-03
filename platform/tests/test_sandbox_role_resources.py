"""Tests for RoleResourceProfile and per-role resource configuration."""

from __future__ import annotations

from unittest.mock import patch

from app.worker.sandbox_backend import (
    RoleResourceProfile,
    SandboxInfo,
    get_role_resource_profile,
)


# ---------------------------------------------------------------------------
# RoleResourceProfile dataclass
# ---------------------------------------------------------------------------


class TestRoleResourceProfile:
    def test_default_values(self) -> None:
        profile = RoleResourceProfile()
        assert profile.cpus == 2
        assert profile.memory_mib == 4096
        assert profile.image is None
        assert profile.mount_mode == "rw"

    def test_custom_values(self) -> None:
        profile = RoleResourceProfile(cpus=4, memory_mib=8192, image="custom:latest", mount_mode="ro")
        assert profile.cpus == 4
        assert profile.memory_mib == 8192
        assert profile.image == "custom:latest"
        assert profile.mount_mode == "ro"


# ---------------------------------------------------------------------------
# SandboxInfo role field
# ---------------------------------------------------------------------------


class TestSandboxInfoRole:
    def test_role_defaults_to_none(self) -> None:
        info = SandboxInfo(task_id="t1", sandbox_name="test")
        assert info.role is None

    def test_role_can_be_set(self) -> None:
        info = SandboxInfo(task_id="t1", sandbox_name="test", role="coding")
        assert info.role == "coding"

    def test_role_mutable(self) -> None:
        info = SandboxInfo(task_id="t1", sandbox_name="test")
        info.role = "test"
        assert info.role == "test"


# ---------------------------------------------------------------------------
# get_role_resource_profile
# ---------------------------------------------------------------------------


class TestGetRoleResourceProfile:
    def test_empty_config_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = "{}"
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2
            assert profile.memory_mib == 4096
            assert profile.mount_mode == "rw"

    def test_missing_role_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = '{"test": {"cpus": 4}}'
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2

    def test_role_with_partial_override(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = '{"test": {"cpus": 4, "memory_mib": 8192}}'
            profile = get_role_resource_profile("test")
            assert profile.cpus == 4
            assert profile.memory_mib == 8192
            assert profile.mount_mode == "rw"
            assert profile.image is None

    def test_role_with_full_override(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = (
                '{"review": {"cpus": 1, "memory_mib": 512, "image": "review:slim", "mount_mode": "ro"}}'
            )
            profile = get_role_resource_profile("review")
            assert profile.cpus == 1
            assert profile.memory_mib == 512
            assert profile.image == "review:slim"
            assert profile.mount_mode == "ro"

    def test_invalid_json_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = "not valid json"
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2

    def test_non_dict_config_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = '["list", "not", "dict"]'
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2

    def test_role_value_not_dict_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            mock_settings.SANDBOX_ROLE_RESOURCES = '{"coding": "invalid"}'
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2

    def test_missing_attribute_returns_defaults(self) -> None:
        with patch("app.config.settings") as mock_settings:
            # Simulate missing attribute
            del mock_settings.SANDBOX_ROLE_RESOURCES
            mock_settings.SANDBOX_ROLE_RESOURCES = "{}"
            profile = get_role_resource_profile("coding")
            assert profile.cpus == 2
