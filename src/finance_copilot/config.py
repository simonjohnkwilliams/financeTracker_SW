"""Layer 4 — application configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and/or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="FINANCE_",
        env_file=".env",
        extra="ignore",
    )

    db_path: Path = Field(default=Path("finance.db"))
    truelayer_environment: str = Field(default="sandbox")
    truelayer_client_id: str = Field(default="")
    truelayer_client_secret: str = Field(default="")
    truelayer_redirect_uri: str = Field(default="http://localhost:8080/oauth2/callback")
    truelayer_providers: str = Field(default="")
    initial_window_days: int = Field(default=90)
    lookback_days: int = Field(default=7)

    @property
    def auth_host(self) -> str:
        from finance_copilot.truelayer.oauth import LIVE_AUTH_HOST, SANDBOX_AUTH_HOST

        return (
            SANDBOX_AUTH_HOST if self.truelayer_environment == "sandbox" else LIVE_AUTH_HOST
        )

    @property
    def api_host(self) -> str:
        from finance_copilot.truelayer.client import LIVE_API_HOST, SANDBOX_API_HOST

        return (
            SANDBOX_API_HOST if self.truelayer_environment == "sandbox" else LIVE_API_HOST
        )

    @property
    def providers(self) -> str:
        from finance_copilot.truelayer.oauth import SANDBOX_DEFAULT_PROVIDERS

        if self.truelayer_providers:
            return self.truelayer_providers
        return SANDBOX_DEFAULT_PROVIDERS if self.truelayer_environment == "sandbox" else ""
