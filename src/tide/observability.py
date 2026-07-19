"""Secret-safe runtime logging and request-correlation primitives."""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime, timezone
import json
import logging
import re
import sys
from types import TracebackType
from typing import Any, TextIO
from uuid import uuid4


CORRELATION_HEADER = "X-Correlation-ID"
_CORRELATION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_EVENT_PATTERN = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "tide_correlation_id",
    default=None,
)
_SAFE_LOG_FIELDS = (
    "channel",
    "correlation_id",
    "operation",
    "method",
    "status_code",
    "duration_ms",
    "error_type",
)


def resolve_correlation_id(candidate: str | None) -> str:
    """Accept one bounded safe identifier or create a server-owned UUID."""

    if candidate is not None and _CORRELATION_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def bind_correlation_id(correlation_id: str) -> Token[str | None]:
    """Bind an already validated identifier to the current async context."""

    return _CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the correlation context that preceded the current request."""

    _CORRELATION_ID.reset(token)


def current_correlation_id() -> str | None:
    """Return the active HTTP correlation identifier, when one exists."""

    return _CORRELATION_ID.get()


def log_runtime_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """Emit a runtime event using only the framework's reviewed field allowlist."""

    if not _EVENT_PATTERN.fullmatch(event):
        raise ValueError("runtime event names must use bounded lowercase dotted tokens")
    safe_fields: dict[str, Any] = {}
    for name in _SAFE_LOG_FIELDS:
        value = fields.get(name)
        if isinstance(value, str):
            safe_fields[name] = value[:128]
        elif isinstance(value, int | float) and not isinstance(value, bool):
            safe_fields[name] = value
    logger.log(
        level,
        event,
        extra={"tide_event": event, "tide_fields": safe_fields},
    )


class TideJsonFormatter(logging.Formatter):
    """Format only reviewed operational fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                timezone.utc,
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "event": str(getattr(record, "tide_event", "runtime.message")),
        }
        fields = getattr(record, "tide_fields", {})
        if isinstance(fields, dict):
            for name in _SAFE_LOG_FIELDS:
                value = fields.get(name)
                if isinstance(value, str | int | float) and not isinstance(value, bool):
                    payload[name] = value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class RuntimeLoggingConfiguration:
    """Restorable configuration for the dedicated TIDE runtime logger."""

    def __init__(
        self,
        logger: logging.Logger,
        handler: logging.Handler,
        *,
        previous_handlers: tuple[logging.Handler, ...],
        previous_level: int,
        previous_propagate: bool,
        previous_disabled: bool,
    ) -> None:
        self.logger = logger
        self.handler = handler
        self.previous_handlers = previous_handlers
        self.previous_level = previous_level
        self.previous_propagate = previous_propagate
        self.previous_disabled = previous_disabled
        self._restored = False

    def restore(self) -> None:
        if self._restored:
            return
        self.logger.handlers[:] = self.previous_handlers
        self.logger.setLevel(self.previous_level)
        self.logger.propagate = self.previous_propagate
        self.logger.disabled = self.previous_disabled
        self.handler.close()
        self._restored = True

    def __enter__(self) -> logging.Logger:
        return self.logger

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.restore()


def configure_runtime_logging(
    level: str = "info",
    *,
    stream: TextIO | None = None,
) -> RuntimeLoggingConfiguration:
    """Configure JSON runtime output while preserving the caller's logger state."""

    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown runtime log level {level!r}")
    logger = logging.getLogger("tide.runtime")
    previous_handlers = tuple(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_disabled = logger.disabled
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(TideJsonFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(numeric_level)
    logger.propagate = False
    logger.disabled = False
    return RuntimeLoggingConfiguration(
        logger,
        handler,
        previous_handlers=previous_handlers,
        previous_level=previous_level,
        previous_propagate=previous_propagate,
        previous_disabled=previous_disabled,
    )
