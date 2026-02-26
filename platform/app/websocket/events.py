"""WebSocket event type constants."""

# Agent events
AGENT_STATUS_CHANGED = "agent:status_changed"
AGENT_SESSION_UPDATE = "agent:session_update"

# Task events
TASK_CREATED = "task:created"
TASK_STATUS_CHANGED = "task:status_changed"
TASK_STAGE_UPDATE = "task:stage_update"
TASK_STAGE_LOG = "task:stage_log"
TASK_LOG_STREAM_UPDATE = "task:log_stream_update"

# Gate events
GATE_CREATED = "gate:created"
GATE_APPROVED = "gate:approved"
GATE_REJECTED = "gate:rejected"

# Circuit breaker events
CB_TRIGGERED = "circuit_breaker:triggered"
CB_RESOLVED = "circuit_breaker:resolved"

# KPI events
KPI_UPDATE = "kpi:update"
