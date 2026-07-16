"""Adapter-independent API contracts with optional FastAPI hosting."""

from __future__ import annotations

from typing import Any

from tide.api.openapi import OpenApiPreview, build_openapi_preview, generate_openapi

__all__ = [
    "BearerAuthenticator",
    "DevelopmentTokenAuthenticator",
    "OpenApiPreview",
    "RemoteActionService",
    "RemoteRecordsService",
    "RemoteReportService",
    "RemoteSecurityView",
    "TideApiClient",
    "TideApiClientError",
    "TideApiContractError",
    "TideApiPage",
    "TideApiRecord",
    "TideApiTransportError",
    "build_fastapi_app",
    "build_openapi_preview",
    "generate_openapi",
]


def __getattr__(name: str) -> Any:
    if name in {
        "RemoteActionService",
        "RemoteRecordsService",
        "RemoteReportService",
        "RemoteSecurityView",
    }:
        from tide.api.remote import (
            RemoteActionService,
            RemoteRecordsService,
            RemoteReportService,
            RemoteSecurityView,
        )

        return {
            "RemoteActionService": RemoteActionService,
            "RemoteRecordsService": RemoteRecordsService,
            "RemoteReportService": RemoteReportService,
            "RemoteSecurityView": RemoteSecurityView,
        }[name]
    if name in {
        "TideApiClient",
        "TideApiClientError",
        "TideApiContractError",
        "TideApiPage",
        "TideApiRecord",
        "TideApiTransportError",
    }:
        from tide.api.client import (
            TideApiClient,
            TideApiClientError,
            TideApiContractError,
            TideApiPage,
            TideApiRecord,
            TideApiTransportError,
        )

        return {
            "TideApiClient": TideApiClient,
            "TideApiClientError": TideApiClientError,
            "TideApiContractError": TideApiContractError,
            "TideApiPage": TideApiPage,
            "TideApiRecord": TideApiRecord,
            "TideApiTransportError": TideApiTransportError,
        }[name]
    if name in {
        "BearerAuthenticator",
        "DevelopmentTokenAuthenticator",
        "build_fastapi_app",
    }:
        from tide.api.server import (
            BearerAuthenticator,
            DevelopmentTokenAuthenticator,
            build_fastapi_app,
        )

        return {
            "BearerAuthenticator": BearerAuthenticator,
            "DevelopmentTokenAuthenticator": DevelopmentTokenAuthenticator,
            "build_fastapi_app": build_fastapi_app,
        }[name]
    raise AttributeError(name)
