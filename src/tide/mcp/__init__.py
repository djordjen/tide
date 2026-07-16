"""Optional runtime MCP adapter over TIDE application services."""

from tide.mcp.contracts import (
    TideMcpEntitySchema,
    TideMcpFieldSchema,
    TideMcpPage,
    TideMcpRecord,
)
from tide.mcp.runtime import (
    RuntimeMcpExposure,
    RuntimeMcpService,
    runtime_mcp_exposures,
)

__all__ = [
    "RuntimeMcpExposure",
    "RuntimeMcpService",
    "TideMcpEntitySchema",
    "TideMcpFieldSchema",
    "TideMcpPage",
    "TideMcpRecord",
    "runtime_mcp_exposures",
]
