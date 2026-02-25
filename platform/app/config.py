from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./agent_platform.db"
    REDIS_URL: str = "redis://localhost:6379"
    JWT_SECRET: str = "dev-secret-change-in-production"
    JWT_ENABLED: bool = False
    SKILLKIT_ENABLED: bool = False
    DEBUG: bool = True

    # LLM configuration
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT: float = 120.0

    # Per-role model routing (JSON string: {"coding": "gpt-4o", "review": "claude-sonnet-4-20250514"})
    # Unspecified roles fall back to LLM_MODEL
    LLM_ROLE_MODEL_MAP: str = "{}"

    # Worker configuration
    WORKER_ENABLED: bool = True
    WORKER_POLL_INTERVAL: float = 5.0
    WORKER_GATE_POLL_INTERVAL: float = 3.0
    WORKER_GATE_MAX_WAIT_SECONDS: int = 3600     # gate max wait 1h
    WORKER_STAGE_MAX_RETRIES: int = 2            # stage LLM failure retry count
    WORKER_STAGE_RETRY_DELAY: float = 5.0        # retry base delay (exponential backoff)
    WORKER_STAGE_TIMEOUT: float = 300.0          # single LLM call timeout (seconds)
    WORKER_TASK_TIMEOUT: float = 1800.0          # entire task timeout (seconds)

    # Database pool
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # Circuit breaker configuration
    CB_MAX_TOKENS_PER_TASK: int = 200000
    CB_MAX_COST_PER_TASK_RMB: float = 50.0
    CB_TOKEN_PRICE_PER_1K: float = 0.01

    # Webhook secrets (empty = skip verification)
    JIRA_WEBHOOK_SECRET: str = ""
    GITLAB_WEBHOOK_SECRET: str = ""

    # External notification (webhook URL for task events)
    NOTIFY_WEBHOOK_URL: str = ""
    NOTIFY_EVENTS: str = "task_failed,task_completed,gate_created"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # GitHub integration (optional, raises rate limit)
    GITHUB_TOKEN: str = ""

    # ROI benchmark: estimated manual effort per task
    ESTIMATED_HOURS_PER_TASK: float = 8.0
    HOURLY_RATE_RMB: float = 150.0

    # Git worktree isolation for coding agents
    WORKTREE_ENABLED: bool = False
    WORKTREE_BASE_DIR: str = "/tmp/silicon_agent/worktrees"
    WORKTREE_AUTO_BRANCH: bool = True  # auto-create feature branches
    WORKTREE_AUTO_PR: bool = False  # auto-create PR on task completion

    # Memory & compression configuration
    MEMORY_ENABLED: bool = True
    MEMORY_COMPRESSION_ENABLED: bool = True
    MEMORY_MAX_ENTRIES_PER_CATEGORY: int = 50
    MEMORY_MAX_CONTEXT_TOKENS: int = 2000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
