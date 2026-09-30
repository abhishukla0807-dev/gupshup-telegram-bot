from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = "mock_token_for_dev"
    DATABASE_URL: str = "postgresql+asyncpg://matchmaker_user:secretpassword@localhost:5444/matchmaker_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    ENVIRONMENT: str = "development"

    # Rate Limiting Configuration
    RATE_LIMIT_CHAT_MAX_MESSAGES: int = 5
    RATE_LIMIT_CHAT_WINDOW_SECONDS: float = 2.0
    RATE_LIMIT_SEARCH_CAPACITY: int = 1
    RATE_LIMIT_SEARCH_REFILL_PERIOD_SECONDS: float = 3.0

    # Content Moderation Configuration
    MODERATION_BLOCK_USERNAMES: bool = True
    MODERATION_BLOCK_URLS: bool = True

    # Matchmaking & Queue Configuration
    MATCHMAKING_HARD_CONSTRAINT_TIMEOUT_SECONDS: float = 10.0
    MATCHMAKING_MAX_QUEUE_WAIT_SECONDS: float = 60.0

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str) -> str:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
