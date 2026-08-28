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
    RuntimeMcpReportExposure,
    RuntimeMcpService,
    runtime_mcp_exposures,
    runtime_mcp_report_exposures,
)

__all__ = [
    "RuntimeMcpActionExposure",
    "RuntimeMcpExposure",
    "RuntimeMcpReportExposure",
    "RuntimeMcpService",
    "TideMcpActionSchema",
    "TideMcpEntitySchema",
    "TideMcpFieldSchema",
    "TideMcpMutationResult",
    "TideMcpPage",
    "TideMcpRecord",
    "runtime_mcp_exposures",
    "runtime_mcp_report_exposures",
]
