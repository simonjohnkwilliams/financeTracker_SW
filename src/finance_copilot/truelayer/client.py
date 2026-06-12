"""Layer 2b — TrueLayerClient: typed wrappers over the TrueLayer Data API."""

from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from finance_copilot.log_config import get_logger
from finance_copilot.truelayer.errors import AuthError, RateLimitError, TransientError

log = get_logger("truelayer.client")

SANDBOX_API_HOST = "https://api.truelayer-sandbox.com"
LIVE_API_HOST = "https://api.truelayer.com"

MAX_RETRIES = 4  # total attempts (1 initial + 3 retries, each with a sleep before)
RETRY_DELAYS = [1, 4, 16]  # seconds to sleep before retry 1, 2, 3 respectively


class TrueLayerClient:
    """Thin typed client for the TrueLayer Data API.

    All HTTP calls go through :meth:`_get` which handles authentication,
    retries, and error mapping.
    """

    def __init__(
        self,
        *,
        api_host: str,
        access_token: str,
        http_client: httpx.Client,
    ) -> None:
        self._api_host = api_host
        self._access_token = access_token
        self._client = http_client

    def fetch_accounts(self) -> list[dict[str, Any]]:
        """Fetch all accounts for the authenticated user."""
        body = self._get(f"{self._api_host}/data/v1/accounts")
        return list(body.get("results", []))

    def fetch_transactions(
        self,
        account_id: str,
        *,
        from_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch transactions for ``account_id``.

        Amounts are decoded as :class:`~decimal.Decimal` at the HTTP boundary.

        :param from_date: If provided, passed as ``from=YYYY-MM-DD`` query parameter.
        """
        params: dict[str, str] = {}
        if from_date is not None:
            params["from"] = from_date.isoformat()
        body = self._get(
            f"{self._api_host}/data/v1/accounts/{account_id}/transactions",
            params=params if params else None,
        )
        if "next" in body or "cursor" in body:
            log.warning(
                "pagination.detected",
                account_id=account_id,
                has_next="next" in body,
                has_cursor="cursor" in body,
            )
        return list(body.get("results", []))

    def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Authenticated GET with retry on 429 and 5xx.

        Retry policy: up to ``MAX_RETRIES`` attempts, sleeping ``RETRY_DELAYS[i]`` seconds
        between each (controlled by :func:`time.sleep`, which is patchable in tests).

        Error mapping:
        - 401 → :exc:`~finance_copilot.truelayer.errors.AuthError`
        - 429 after all retries → :exc:`~finance_copilot.truelayer.errors.RateLimitError`
        - 5xx after all retries → :exc:`~finance_copilot.truelayer.errors.TransientError`
        - Other 4xx → raises :exc:`httpx.HTTPStatusError` immediately (no retry)
        """
        headers = {"Authorization": f"Bearer {self._access_token}"}
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params

        last_response: httpx.Response | None = None

        for attempt in range(MAX_RETRIES):
            # Sleep before each retry attempt (not before the first attempt)
            if attempt > 0:
                time.sleep(RETRY_DELAYS[attempt - 1])

            response = self._client.get(url, **kwargs)

            if response.status_code == 200:
                result: dict[str, Any] = json.loads(response.content, parse_float=Decimal)
                return result

            if response.status_code == 401:
                raise AuthError("TrueLayer returned 401 — token may be expired or revoked")

            if response.status_code == 429 or response.status_code >= 500:
                last_response = response
                continue

            # Other 4xx — do not retry
            response.raise_for_status()

        # Exhausted retries
        assert last_response is not None
        if last_response.status_code == 429:
            raise RateLimitError(
                f"TrueLayer rate limit still active after {MAX_RETRIES} attempts"
            )
        raise TransientError(
            f"TrueLayer returned {last_response.status_code} after {MAX_RETRIES} attempts"
        )
