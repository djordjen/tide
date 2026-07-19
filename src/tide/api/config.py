"""Typed operational limits for the hosted TIDE HTTP boundary."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MAX_REQUEST_BODY_BYTES = 1_048_576
DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CONCURRENT_REQUESTS = 100
DEFAULT_KEEP_ALIVE_TIMEOUT_SECONDS = 5
DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class HttpServerLimits:
    """Validated limits shared by CLI hosting and the FastAPI adapter."""

    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES
    request_body_timeout_seconds: int = DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS
    keep_alive_timeout_seconds: int = DEFAULT_KEEP_ALIVE_TIMEOUT_SECONDS
    graceful_shutdown_timeout_seconds: int = (
        DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    )

    def __post_init__(self) -> None:
        _require_positive(
            self.max_request_body_bytes,
            "maximum request body size",
        )
        _require_positive(
            self.request_body_timeout_seconds,
            "request body timeout",
        )
        _require_positive(
            self.max_concurrent_requests,
            "maximum concurrent requests",
        )
        _require_non_negative(
            self.keep_alive_timeout_seconds,
            "keep-alive timeout",
        )
        _require_positive(
            self.graceful_shutdown_timeout_seconds,
            "graceful shutdown timeout",
        )


def _require_positive(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _require_non_negative(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
