"""API-specific environment settings."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from shared.settings import AppSettings


class ApiSettings(AppSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_key: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    database_url: str = (
        "postgresql+asyncpg://store_intel:store_intel_dev@localhost:5432/store_intel"
    )
