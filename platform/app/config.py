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
    # Comma-separated absolute path prefixes allowed in agent config `extra_skill_dirs`.
    # Empty means only built-in platform/skills directory is allowed.
    EXTRA_SKILL_DIR_WHITELIST: str = ""

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

    # GitHub Enterprise integration (e.g. scm.starbucks.com)
    GHE_BASE_URL: str = ""       # e.g. "https://scm.starbucks.com/api/v3"
    GHE_TOKEN: str = ""          # Personal access token for the GHE instance

    # ROI benchmark: estimated manual effort per task
    ESTIMATED_HOURS_PER_TASK: float = 8.0
    HOURLY_RATE_RMB: float = 150.0

    # Git worktree isolation for coding agents
    WORKTREE_ENABLED: bool = False
    WORKTREE_BASE_DIR: str = "/var/lib/silicon_agent/worktrees"
    WORKTREE_AUTO_PR: bool = False  # auto-create PR on task completion

    # Container sandbox configuration
    SANDBOX_ENABLED: bool = False
    SANDBOX_IMAGE: str = "silicon-agent-sandbox:coding"
    SANDBOX_CPUS: float = 2.0
    SANDBOX_MEMORY: str = "4g"
    SANDBOX_PIDS_LIMIT: int = 256
    SANDBOX_NETWORK: str = "sa-sandbox-net"
    SANDBOX_AGENT_PORT: int = 9090
    SANDBOX_READONLY_ROOT: bool = True
    SANDBOX_MAX_CONCURRENT: int = 4
    SANDBOX_WORKSPACE_BASE_DIR: str = "/tmp/silicon_agent/tasks"
    SANDBOX_FALLBACK_MODE: str = "graceful"

    # Memory & compression configuration
    MEMORY_ENABLED: bool = True
    MEMORY_COMPRESSION_ENABLED: bool = True
    MEMORY_MAX_ENTRIES_PER_CATEGORY: int = 50
    MEMORY_MAX_CONTEXT_TOKENS: int = 2000

    # Phase 1: Structured contracts & failure handling
    STAGE_CONTRACTS_ENABLED: bool = True
    FAILURE_AUTO_RETRY_CATEGORIES: str = "transient,tool_error"
    GATE_DEFAULT_MAX_RETRIES: int = 2

    # Phase 2: Adaptive orchestration
    CONDITIONS_ENABLED: bool = True
    EVALUATOR_DEFAULT_MIN_CONFIDENCE: float = 0.7
    EVALUATOR_MAX_ITERATIONS: int = 3
    DYNAMIC_GATE_ENABLED: bool = False
    DYNAMIC_GATE_CONFIDENCE_THRESHOLD: float = 0.5
    STAGE_DEFAULT_MAX_RETRIES: int = 3

    # Phase 3: Intelligent orchestration
    GRAPH_EXECUTION_ENABLED: bool = False
    GRAPH_MAX_LOOP_ITERATIONS: int = 5
    INTERACTIVE_PLANNING_ENABLED: bool = False
    INTERACTIVE_PLANNING_TEMPLATES: str = "full_pipeline"
    DYNAMIC_ROUTING_ENABLED: bool = False
    DYNAMIC_ROUTING_MODEL: str = ""
    TEMPLATE_VERSIONING_ENABLED: bool = False

    # Skill self-learning (Phase 1)
    SKILL_FEEDBACK_ENABLED: bool = True       # Enable skill effectiveness metrics
    SKILL_REFLECTION_ENABLED: bool = True     # Enable structured reflection on failure
    SKILL_REFLECTION_MODEL: str = ""          # Model for reflection (empty = default)

    # Task log pipeline
    TASK_LOG_PIPELINE_QUEUE_SIZE: int = 4000
    TASK_LOG_PIPELINE_FLUSH_INTERVAL_SECONDS: float = 1.0
    TASK_LOG_PIPELINE_BATCH_SIZE: int = 200

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
