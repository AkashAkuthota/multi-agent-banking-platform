"""Application configuration."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(env_file=".env")

    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int
    API_DOCS_ENABLED: bool = True
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 900
    # Local-only 32-byte url-safe Base64 AES-GCM key. Replace before any shared deploy.
    SESSION_ENCRYPTION_KEY: SecretStr = SecretStr(
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    CHAT_SESSION_TTL_MINUTES: int = 30
    LLM_PROVIDER: Literal["openai", "gemini", "ollama"] = "openai"
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str | None = None
    GEMINI_API_KEY: SecretStr | None = None
    GEMINI_MODEL: str | None = None
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434/v1/"
    OLLAMA_MODEL: str = "qwen3:1.7b"

settings = Settings()
