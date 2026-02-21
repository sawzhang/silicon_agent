from __future__ import annotations

import logging
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)

try:
    from skillkit import AgentRunner  # type: ignore[import-untyped]
    SKILLKIT_AVAILABLE = True
except ImportError:
    SKILLKIT_AVAILABLE = False
    AgentRunner = None

AGENT_ROLES = ["orchestrator", "spec", "coding", "test", "review", "smoke", "doc"]


class MockBridge:
    """Mock bridge used when SkillKit is not available."""

    def __init__(self) -> None:
        self._agents: Dict[str, Dict] = {
            role: {
                "role": role,
                "status": "idle",
                "model": "claude-sonnet-4-20250514",
                "session": None,
            }
            for role in AGENT_ROLES
        }

    async def start_agent(self, role: str) -> dict:
        if role not in self._agents:
            raise ValueError(f"Unknown agent role: {role}")
        self._agents[role]["status"] = "running"
        return {"role": role, "status": "running"}

    async def stop_agent(self, role: str) -> dict:
        if role not in self._agents:
            raise ValueError(f"Unknown agent role: {role}")
        self._agents[role]["status"] = "idle"
        self._agents[role]["session"] = None
        return {"role": role, "status": "idle"}

    async def get_agent_status(self, role: str) -> dict:
        if role not in self._agents:
            raise ValueError(f"Unknown agent role: {role}")
        return self._agents[role]

    async def send_message(self, role: str, message: str) -> dict:
        return {
            "role": role,
            "response": f"[Mock] Agent '{role}' received: {message}",
            "tokens_used": 0,
        }


class SkillKitBridge:
    """Bridge to real SkillKit AgentRunner."""

    def __init__(self) -> None:
        self._runner = None

    async def initialize(self) -> None:
        if SKILLKIT_AVAILABLE and AgentRunner is not None:
            self._runner = AgentRunner()
            logger.info("SkillKit AgentRunner initialized")
        else:
            logger.warning("SkillKit not available")

    async def start_agent(self, role: str) -> dict:
        if self._runner is None:
            raise RuntimeError("SkillKit not initialized")
        return {"role": role, "status": "running"}

    async def stop_agent(self, role: str) -> dict:
        if self._runner is None:
            raise RuntimeError("SkillKit not initialized")
        return {"role": role, "status": "idle"}

    async def send_message(self, role: str, message: str) -> dict:
        if self._runner is None:
            raise RuntimeError("SkillKit not initialized")
        return {"role": role, "response": "", "tokens_used": 0}


_bridge: Optional[Union[MockBridge, SkillKitBridge]] = None


async def init_bridge(use_skillkit: bool = False) -> Union[MockBridge, SkillKitBridge]:
    global _bridge
    if use_skillkit and SKILLKIT_AVAILABLE:
        _bridge = SkillKitBridge()
        await _bridge.initialize()
        logger.info("Using SkillKit bridge")
    else:
        _bridge = MockBridge()
        logger.info("Using mock bridge (SkillKit not available)")
    return _bridge


def get_bridge() -> Union[MockBridge, SkillKitBridge]:
    if _bridge is None:
        raise RuntimeError("Bridge not initialized. Call init_bridge() first.")
    return _bridge
