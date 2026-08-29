"""Dashboard settings."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from shared.settings import AppSettings


class DashboardSettings(AppSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    dashboard_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias="DASHBOARD_API_URL",
    )
    dashboard_refresh_seconds: int = Field(
        default=2,
        validation_alias="DASHBOARD_REFRESH_SECONDS",
    )
    default_store_id: str = Field(
        default="store-001",
        validation_alias="DEFAULT_STORE_ID",
    )
