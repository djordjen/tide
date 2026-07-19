from __future__ import annotations

import pytest

from tide.api.config import HttpServerLimits


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"max_request_body_bytes": 0},
            "maximum request body size must be a positive integer",
        ),
        (
            {"request_body_timeout_seconds": 0},
            "request body timeout must be a positive integer",
        ),
        (
            {"max_concurrent_requests": -1},
            "maximum concurrent requests must be a positive integer",
        ),
        (
            {"keep_alive_timeout_seconds": -1},
            "keep-alive timeout must be a non-negative integer",
        ),
        (
            {"graceful_shutdown_timeout_seconds": 0},
            "graceful shutdown timeout must be a positive integer",
        ),
    ],
)
def test_http_server_limits_fail_closed(
    overrides: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        HttpServerLimits(**overrides)


def test_http_server_limit_defaults_are_production_bounded() -> None:
    limits = HttpServerLimits()

    assert limits.max_request_body_bytes == 1_048_576
    assert limits.request_body_timeout_seconds == 30
    assert limits.max_concurrent_requests == 100
    assert limits.keep_alive_timeout_seconds == 5
    assert limits.graceful_shutdown_timeout_seconds == 30
