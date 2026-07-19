"""Optional runtime MCP adapter over TIDE application services."""

from tide.mcp.contracts import (
    TideMcpActionSchema,
    TideMcpEntitySchema,
    TideMcpFieldSchema,
    TideMcpMutationResult,
    TideMcpPage,
    TideMcpRecord,
)
from tide.mcp.runtime import (
    RuntimeMcpActionExposure,
    RuntimeMcpExposure,
    RuntimeMcpService,
    runtime_mcp_exposures,
)

__all__ = [
    "RuntimeMcpActionExposure",
    "RuntimeMcpExposure",
    "RuntimeMcpService",
    "TideMcpActionSchema",
    "TideMcpEntitySchema",
    "TideMcpFieldSchema",
    "TideMcpMutationResult",
    "TideMcpPage",
    "TideMcpRecord",
    "runtime_mcp_exposures",
]
