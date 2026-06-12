"""Tests for log_config.py — redact_sensitive processor coverage (TD-2)."""
from __future__ import annotations

from finance_copilot.log_config import SENSITIVE_KEYS, redact_sensitive


class TestRedactSensitive:
    def test_redacts_access_token(self) -> None:
        event: dict[str, str] = {"event": "test", "access_token": "secret123"}
        result = redact_sensitive(None, "info", event)
        assert result["access_token"] == "<redacted>"

    def test_redacts_refresh_token(self) -> None:
        event: dict[str, str] = {"event": "test", "refresh_token": "myrefreshtoken"}
        result = redact_sensitive(None, "info", event)
        assert result["refresh_token"] == "<redacted>"

    def test_redacts_raw_payload(self) -> None:
        event: dict[str, str] = {
            "event": "test",
            "raw_payload": '{"account_number": "12345678"}',
        }
        result = redact_sensitive(None, "info", event)
        assert result["raw_payload"] == "<redacted>"

    def test_redacts_account_number(self) -> None:
        event: dict[str, str] = {"event": "test", "account_number": "12345678"}
        result = redact_sensitive(None, "info", event)
        assert result["account_number"] == "<redacted>"

    def test_redacts_iban(self) -> None:
        event: dict[str, str] = {"event": "test", "iban": "GB29NWBK60161331926819"}
        result = redact_sensitive(None, "info", event)
        assert result["iban"] == "<redacted>"

    def test_passes_through_non_sensitive_keys(self) -> None:
        event: dict[str, str] = {"event": "sync.start", "run_id": "abc123", "status": "running"}
        result = redact_sensitive(None, "info", event)
        assert result["run_id"] == "abc123"
        assert result["status"] == "running"
        assert result["event"] == "sync.start"

    def test_handles_missing_sensitive_keys_gracefully(self) -> None:
        event: dict[str, str] = {"event": "sync.start"}
        result = redact_sensitive(None, "info", event)
        assert result == {"event": "sync.start"}

    def test_redacts_all_sensitive_keys_simultaneously(self) -> None:
        event: dict[str, str] = {k: f"value_{k}" for k in SENSITIVE_KEYS}
        event["event"] = "test"
        result = redact_sensitive(None, "info", event)
        for key in SENSITIVE_KEYS:
            assert result[key] == "<redacted>"
        assert result["event"] == "test"

    def test_returns_the_same_event_dict(self) -> None:
        event: dict[str, str] = {"event": "test"}
        result = redact_sensitive(None, "info", event)
        assert result is event
