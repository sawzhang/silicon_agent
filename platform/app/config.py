from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./agent_platform.db"
    REDIS_URL: str = "redis://localhost:6379"
    JWT_SECRET: str = "dev-secret-change-in-production"
    JWT_ENABLED: bool = False
    SKILLKIT_ENABLED: bool = False
    DEBUG: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
