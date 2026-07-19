from __future__ import annotations

from io import StringIO
import json
import logging
from uuid import UUID

from tide.observability import (
    configure_runtime_logging,
    log_runtime_event,
    resolve_correlation_id,
)


def test_structured_runtime_logs_emit_only_reviewed_fields() -> None:
    stream = StringIO()
    configuration = configure_runtime_logging("info", stream=stream)
    try:
        log_runtime_event(
            configuration.logger,
            logging.WARNING,
            "http.request.completed",
            channel="rest",
            correlation_id="request-123",
            operation="list_catalog_Product",
            method="GET",
            status_code=401,
            duration_ms=1.25,
            bearer_token="must-not-leak",
            request_body={"password": "must-not-leak"},
        )
    finally:
        configuration.restore()

    payload = json.loads(stream.getvalue())
    assert payload["timestamp"].endswith("Z")
    assert set(payload) == {
        "timestamp",
        "level",
        "event",
        "channel",
        "correlation_id",
        "operation",
        "method",
        "status_code",
        "duration_ms",
    }
    assert payload["level"] == "warning"
    assert payload["event"] == "http.request.completed"
    assert payload["channel"] == "rest"
    assert payload["correlation_id"] == "request-123"
    assert payload["operation"] == "list_catalog_Product"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 401
    assert payload["duration_ms"] == 1.25
    assert "must-not-leak" not in stream.getvalue()
    assert "bearer_token" not in payload
    assert "request_body" not in payload


def test_correlation_identifiers_are_bounded_and_log_safe() -> None:
    assert resolve_correlation_id("caller.request:123") == "caller.request:123"

    for invalid in ("", " contains-space", "line\nbreak", "x" * 129):
        generated = resolve_correlation_id(invalid)
        UUID(generated)
        assert generated != invalid


def test_direct_logger_messages_cannot_bypass_the_structured_allowlist() -> None:
    stream = StringIO()
    configuration = configure_runtime_logging("info", stream=stream)
    try:
        configuration.logger.error("password=must-not-leak")
    finally:
        configuration.restore()

    assert json.loads(stream.getvalue())["event"] == "runtime.message"
    assert "must-not-leak" not in stream.getvalue()
