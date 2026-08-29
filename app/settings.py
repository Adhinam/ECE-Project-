"""Environment-based API configuration."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.settings import AppSettings


class Settings(AppSettings):
    """
    Application settings loaded from environment variables and `.env`.

    Prefix: none for shared vars; database uses DATABASE_URL directly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # HTTP
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_key: str | None = Field(default=None, validation_alias="API_KEY")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8501", "http://localhost:3000"]
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://store_intel:store_intel_dev@localhost:5432/store_intel",
        validation_alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = Field(default=1800, ge=60, description="Recycle connections after N seconds")
    db_connect_max_retries: int = Field(default=5, ge=1)
    db_connect_retry_seconds: float = Field(default=2.0, ge=0.1)

    # OpenAPI
    api_title: str = "Store Intelligence API"
    api_version: str = Field(default="0.1.0", validation_alias="API_VERSION")
    api_description: str = (
        "Retail analytics API — event ingestion, real-time metrics, funnel, "
        "heatmap, and anomaly endpoints."
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v  # type: ignore[return-value]


# Backward-compatible alias
ApiSettings = Settings
