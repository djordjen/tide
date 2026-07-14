"""Identity and request context shared by every adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class Channel(StrEnum):
    TUI = "tui"
    REST = "rest"
    WEB = "web"
    MCP = "mcp"
    REPORT = "report"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Principal:
    identifier: str
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal
    locale: str = "en"
    timezone: str = "UTC"
    channel: Channel = Channel.SYSTEM
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
