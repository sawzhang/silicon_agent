from __future__ import annotations

import re

_COMMAND_PATTERN = re.compile(
    r"(?<![\w/])(?P<token>@silicon_agent|/silicon_agent)\b"
)


def parse_silicon_agent_command(comment_body: str | None) -> dict[str, str | bool | None]:
    """Parse a silicon_agent mention or slash command from an issue comment."""
    text = str(comment_body or "")
    match = _COMMAND_PATTERN.search(text)
    if match is None:
        return {
            "silicon_agent_command_triggered": False,
            "silicon_agent_command_style": None,
            "silicon_agent_command_text": None,
            "silicon_agent_command_note": None,
        }

    token = match.group("token")
    note = f"{text[:match.start()].strip()} {text[match.end():].strip()}".strip()
    style = "mention" if token.startswith("@") else "slash"
    return {
        "silicon_agent_command_triggered": True,
        "silicon_agent_command_style": style,
        "silicon_agent_command_text": token,
        "silicon_agent_command_note": note or None,
    }
