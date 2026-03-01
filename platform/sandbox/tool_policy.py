"""Shared tool policy utilities used by local worker and sandbox agent server.

This module centralizes:
1) dynamic tool catalog discovery from SkillKit's ``runner.get_tools()``,
2) argument schema hint generation,
3) argument parsing/validation and actionable error construction,
4) a reusable execution-policy mixin for tool-call normalization and gating.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from typing import Any

DEFAULT_FALLBACK_CORE_TOOLS: set[str] = {
    "read",
    "write",
    "execute",
    "execute_script",
    "skill",
}

DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS: dict[str, str] = {
    "execute": '{"command":"<shell command>","cwd":"<optional path>"}',
    "execute_script": '{"script":"<shell script>","cwd":"<optional path>"}',
    "read": '{"path":"<file path>"}',
    "write": '{"path":"<file path>","content":"<file content>"}',
    "skill": '{"name":"<skill name>","arguments":"<optional string>"}',
}


def schema_to_hint(parameters: dict[str, Any]) -> str:
    """Convert JSON schema properties into a compact hint example."""
    properties = parameters.get("properties")
    if not isinstance(properties, dict) or not properties:
        return '{"...":"..."}'
    example: dict[str, Any] = {}
    for key, spec in properties.items():
        if not isinstance(key, str):
            continue
        type_name = spec.get("type") if isinstance(spec, dict) else None
        if type_name == "string":
            example[key] = f"<{key}>"
        elif type_name == "integer":
            example[key] = 0
        elif type_name == "number":
            example[key] = 0
        elif type_name == "boolean":
            example[key] = False
        elif type_name == "array":
            example[key] = []
        elif type_name == "object":
            example[key] = {}
        else:
            example[key] = "<value>"
    return json.dumps(example, ensure_ascii=False, separators=(",", ":"))


def discover_tool_catalog(
    *,
    create_probe_runner: Callable[[], Any],
    fallback_core_tools: Iterable[str] = DEFAULT_FALLBACK_CORE_TOOLS,
    fallback_hints: dict[str, str] = DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
    logger: Any | None = None,
    warning_message: str = "Failed to discover tool catalog from SkillKit; using fallback",
) -> tuple[set[str], dict[str, str]]:
    """Discover tool names and hints from a probe runner."""
    fallback_tools = set(fallback_core_tools)
    fallback_hint_map = dict(fallback_hints)
    try:
        probe = create_probe_runner()
        discovered_tools: set[str] = set()
        discovered_hints: dict[str, str] = {}
        for tool in probe.get_tools():
            if not isinstance(tool, dict):
                continue
            function_info = tool.get("function")
            if not isinstance(function_info, dict):
                continue
            name = function_info.get("name")
            parameters = function_info.get("parameters")
            if not isinstance(name, str):
                continue
            discovered_tools.add(name)
            if isinstance(parameters, dict):
                discovered_hints[name] = schema_to_hint(parameters)
        all_tools = fallback_tools | discovered_tools
        hints = dict(fallback_hint_map)
        hints.update(discovered_hints)
        return all_tools, hints
    except Exception:
        if logger is not None:
            logger.warning(warning_message, exc_info=True)
        return fallback_tools, fallback_hint_map


def sanitize_requested_tools(
    requested_tools: Iterable[str],
    known_tools: set[str],
) -> tuple[set[str], list[str]]:
    """Split requested tool names into allowed and unknown subsets."""
    requested = {name for name in requested_tools if isinstance(name, str)}
    unknown = sorted(name for name in requested if name not in known_tools)
    allowed = requested & known_tools
    return allowed, unknown


def build_invalid_tool_args_error(
    *,
    tool_name: str,
    raw_args: Any,
    detail: str,
    hints: dict[str, str],
    received_type: str | None = None,
) -> str:
    expected = hints.get(tool_name, '{"...":"..."}')
    if isinstance(raw_args, str):
        preview = raw_args
    else:
        preview = json.dumps(raw_args, ensure_ascii=False, default=str)
    preview = preview.replace("\n", "\\n")
    if len(preview) > 160:
        preview = f"{preview[:160]}..."
    type_hint = f"Received type: {received_type}. " if received_type else ""
    return (
        f"Error: Invalid arguments for tool {tool_name}. "
        f"{detail}. "
        f"{type_hint}"
        f"Expected format: {expected}. "
        "Please resend this tool call with a valid JSON object in function.arguments. "
        "If arguments were truncated, split content and retry. "
        f"arguments_preview={preview}"
    )


def parse_tool_arguments(
    *,
    tool_name: str,
    raw_args: Any,
    hints: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse tool arguments and return either dict args or actionable error."""
    if isinstance(raw_args, dict):
        parsed_args: Any = raw_args
    elif isinstance(raw_args, str):
        try:
            parsed_args = json.loads(raw_args or "{}")
        except json.JSONDecodeError as exc:
            return None, build_invalid_tool_args_error(
                tool_name=tool_name,
                raw_args=raw_args,
                detail=f"JSON decode error: {exc.msg} at line {exc.lineno}, column {exc.colno}",
                hints=hints,
            )
    else:
        parsed_args = raw_args
    if not isinstance(parsed_args, dict):
        return None, build_invalid_tool_args_error(
            tool_name=tool_name,
            raw_args=raw_args,
            detail="Arguments must decode to a JSON object",
            hints=hints,
            received_type=type(parsed_args).__name__,
        )
    return parsed_args, None


class ToolExecutionPolicyMixin:
    """Reusable tool-execution policy with validation, normalization and gating."""

    allowed_tools: set[str]
    default_cwd: str | None
    _tool_argument_hints: dict[str, str]

    def _resolve_tool_argument_hints(self) -> dict[str, str]:
        hints = getattr(self, "_tool_argument_hints", None)
        if isinstance(hints, dict) and hints:
            return hints
        return dict(DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS)

    async def _execute_tool_base(self, tool_call: dict[str, Any], on_output=None) -> str:
        raise NotImplementedError

    def _on_tool_validation_error(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        error_msg: str,
        started_at: float,
    ) -> str:
        return error_msg

    def _on_tool_disallowed(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        error_msg: str,
        started_at: float,
    ) -> str:
        return error_msg

    def _on_tool_result(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        started_at: float,
    ) -> str:
        return result

    def _preprocess_validated_tool_call(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        tool_call: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str | None, str | None]:
        return tool_call, args, None, None

    async def _execute_tool_with_policy(self, tool_call, on_output=None) -> str:
        name = str(tool_call.get("name", ""))
        started = time.monotonic()
        raw_args = tool_call.get("arguments", "{}")
        args, parse_error = parse_tool_arguments(
            tool_name=name,
            raw_args=raw_args,
            hints=self._resolve_tool_argument_hints(),
        )
        if parse_error:
            return self._on_tool_validation_error(
                tool_name=name,
                args={},
                error_msg=parse_error,
                started_at=started,
            )
        assert args is not None

        if self.default_cwd and name in ("execute", "execute_script") and not args.get("cwd"):
            args["cwd"] = self.default_cwd
        normalized_tool_call = {**tool_call, "arguments": json.dumps(args, ensure_ascii=False)}

        if name not in self.allowed_tools:
            disallowed_error = f"Error: {name} is not allowed for this role"
            return self._on_tool_disallowed(
                tool_name=name,
                args=args,
                error_msg=disallowed_error,
                started_at=started,
            )

        normalized_tool_call, args, preprocess_error, preprocess_result = self._preprocess_validated_tool_call(
            tool_name=name,
            args=args,
            tool_call=normalized_tool_call,
        )
        if preprocess_error:
            return self._on_tool_validation_error(
                tool_name=name,
                args=args,
                error_msg=preprocess_error,
                started_at=started,
            )
        if preprocess_result is not None:
            return self._on_tool_result(
                tool_name=name,
                args=args,
                result=preprocess_result,
                started_at=started,
            )

        result = await self._execute_tool_base(normalized_tool_call, on_output=on_output)
        return self._on_tool_result(
            tool_name=name,
            args=args,
            result=result,
            started_at=started,
        )
