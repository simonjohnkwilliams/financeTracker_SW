"""Layer 1e — TokenRepository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from finance_copilot.db import oauth_tokens_table

REFRESH_SKEW_SECONDS = 60


class TokenRepository:
    """Single-row token store keyed by provider name (always ``'truelayer'`` in M1)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put(
        self,
        provider: str,
        access_token: str,
        refresh_token: str,
        expires_at: str,
        obtained_at: str,
    ) -> None:
        """Persist a token, overwriting any existing row for the same provider."""
        stmt = insert(oauth_tokens_table).values(
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            obtained_at=obtained_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider"],
            set_={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "obtained_at": obtained_at,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get(self, provider: str) -> dict[str, Any] | None:
        """Return the token row for ``provider``, or ``None`` if not stored."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(oauth_tokens_table).where(oauth_tokens_table.c.provider == provider)
            ).mappings().first()
        return dict(row) if row is not None else None

    def is_due_for_refresh(self, provider: str) -> bool:
        """Return ``True`` if the token expires within ``REFRESH_SKEW_SECONDS`` or is absent."""
        row = self.get(provider)
        if row is None:
            return True
        expires_at = datetime.fromisoformat(row["expires_at"])
        threshold = datetime.now(UTC) + timedelta(seconds=REFRESH_SKEW_SECONDS)
        return expires_at <= threshold
