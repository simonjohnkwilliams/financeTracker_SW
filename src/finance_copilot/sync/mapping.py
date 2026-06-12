"""Layer 0b+0c — TrueLayer payload → database row dict mapping.

Pure functions: no DB, no HTTP.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any


def map_transaction(
    payload: dict[str, Any],
    *,
    account_id: str,
    ingested_at: str,
) -> dict[str, Any]:
    """Map a TrueLayer transaction payload to a ``transactions`` table row dict.

    Raises ``ValueError`` if ``transaction_id`` is missing (fail-fast on bad payloads).

    - ``dedup_key`` = ``"tl:<transaction_id>"``
    - ``booking_date`` = date portion of ``payload["timestamp"]``
    - ``value_date`` = ``None`` (TrueLayer does not provide it)
    - ``amount`` = ``str(Decimal(str(payload["amount"])))`` — Decimal-safe serialisation
    - ``raw_payload`` = ``json.dumps(payload)``
    """
    transaction_id = payload.get("transaction_id")
    if not transaction_id:
        raise ValueError("transaction_id is required but missing from payload")

    raw_amount = payload["amount"]
    amount_str = str(Decimal(str(raw_amount)))

    return {
        "account_id": account_id,
        "dedup_key": f"tl:{transaction_id}",
        "source_transaction_id": transaction_id,
        "booking_date": str(payload["timestamp"])[:10],
        "value_date": None,
        "amount": amount_str,
        "currency": payload["currency"],
        "transaction_type": payload["transaction_type"],
        "description": payload["description"],
        "provider_category": payload.get("transaction_category"),
        "raw_payload": json.dumps(payload, default=str),
        "ingested_at": ingested_at,
    }


def map_account(
    payload: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Map a TrueLayer account payload to an ``accounts`` table row dict.

    Sensitive ``account_number`` sub-object goes into ``raw_payload`` only — it is
    deliberately excluded from top-level columns.

    Both ``first_seen_at`` and ``last_seen_at`` are set to ``now``; the caller's
    upsert logic must preserve ``first_seen_at`` on conflict.
    """
    provider_id: str = ""
    provider = payload.get("provider")
    if isinstance(provider, dict):
        provider_id = str(provider.get("provider_id", ""))

    return {
        "account_id": payload["account_id"],
        "provider_id": provider_id,
        "account_type": payload["account_type"],
        "display_name": payload["display_name"],
        "currency": payload["currency"],
        "first_seen_at": now,
        "last_seen_at": now,
        "raw_payload": json.dumps(payload, default=str),
    }


def make_content_dedup_key(data: dict[str, Any]) -> str:
    """Return ``'content:<sha256>'`` for non-TrueLayer sources (CSV adapter, future use).

    The hash is computed over the JSON-serialised dict with sorted keys so the result
    is deterministic regardless of insertion order.
    """
    serialised = json.dumps(data, sort_keys=True, default=str)
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return f"content:{digest}"
