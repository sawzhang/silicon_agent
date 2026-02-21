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

    # Worker configuration
    WORKER_ENABLED: bool = True
    WORKER_POLL_INTERVAL: float = 5.0
    WORKER_GATE_POLL_INTERVAL: float = 3.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
