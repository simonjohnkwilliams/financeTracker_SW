"""Layer 4 — application configuration via pydantic-settings.

Reads from both ``.env`` and ``creds`` in the current working directory, plus
process environment variables (which take precedence). Field names follow the
existing ``TRUELAYER_*`` convention from the Phase 0 spike rather than a
``FINANCE_`` prefix, so the user's existing ``creds`` file works unchanged.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from ``.env``, ``creds``, and process env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "creds"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    db_path: Path = Field(default=Path("finance.db"), validation_alias="FINANCE_DB_PATH")
    truelayer_environment: str = Field(
        default="sandbox",
        validation_alias=AliasChoices("TRUELAYER_ENVIRONMENT", "FINANCE_TRUELAYER_ENVIRONMENT"),
    )
    truelayer_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("TRUELAYER_CLIENT_ID", "FINANCE_TRUELAYER_CLIENT_ID"),
    )
    truelayer_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("TRUELAYER_CLIENT_SECRET", "FINANCE_TRUELAYER_CLIENT_SECRET"),
    )
    truelayer_redirect_uri: str = Field(
        default="http://localhost:8080/oauth2/callback",
        validation_alias=AliasChoices("TRUELAYER_REDIRECT_URI", "FINANCE_TRUELAYER_REDIRECT_URI"),
    )
    truelayer_providers: str = Field(
        default="",
        validation_alias=AliasChoices("TRUELAYER_PROVIDERS", "FINANCE_TRUELAYER_PROVIDERS"),
    )
    initial_window_days: int = Field(default=90, validation_alias="FINANCE_INITIAL_WINDOW_DAYS")
    lookback_days: int = Field(default=7, validation_alias="FINANCE_LOOKBACK_DAYS")

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
