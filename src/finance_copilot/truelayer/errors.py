"""Finance Copilot exception hierarchy."""

from __future__ import annotations


class FinanceCopilotError(Exception):
    """Base class for all Finance Copilot errors."""


class AuthError(FinanceCopilotError):
    """OAuth failure, token revoked, or missing token."""


class RateLimitError(FinanceCopilotError):
    """TrueLayer rate limit exceeded (429) after all retries."""


class TransientError(FinanceCopilotError):
    """Transient HTTP or network error after all retries."""


class SyncBlockedError(FinanceCopilotError):
    """Attempt to start a sync while one is already running."""


class MappingError(FinanceCopilotError):
    """Error mapping a provider payload to a database row."""


class SchemaVersionError(FinanceCopilotError):
    """DB schema version is newer than the running code version."""
