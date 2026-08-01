"""Adapter-independent API contracts with optional FastAPI hosting."""

from __future__ import annotations

from typing import Any

from tide.api.config import HttpServerLimits
from tide.api.openapi import OpenApiPreview, build_openapi_preview, generate_openapi

__all__ = [
    "BearerAuthenticator",
    "BrowserAuthenticationError",
    "DevelopmentTokenAuthenticator",
    "HttpServerLimits",
    "LocalAuthenticationError",
    "LocalPasswordAuth",
    "LocalUserStore",
    "OidcDiscoveryError",
    "OidcBrowserAuth",
    "OidcBrowserProviderInfo",
    "OidcJwtAuthenticator",
    "OpenApiPreview",
    "RemoteActionService",
    "RemoteAuditHistoryService",
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
        "LocalAuthenticationError",
        "LocalPasswordAuth",
        "LocalUserStore",
    }:
        from tide.api.local_auth import (
            LocalAuthenticationError,
            LocalPasswordAuth,
            LocalUserStore,
        )

        return {
            "LocalAuthenticationError": LocalAuthenticationError,
            "LocalPasswordAuth": LocalPasswordAuth,
            "LocalUserStore": LocalUserStore,
        }[name]
    if name in {
        "BrowserAuthenticationError",
        "OidcBrowserAuth",
        "OidcBrowserProviderInfo",
    }:
        from tide.api.browser_auth import (
            BrowserAuthenticationError,
            OidcBrowserAuth,
            OidcBrowserProviderInfo,
        )

        return {
            "BrowserAuthenticationError": BrowserAuthenticationError,
            "OidcBrowserAuth": OidcBrowserAuth,
            "OidcBrowserProviderInfo": OidcBrowserProviderInfo,
        }[name]
    if name in {
        "OidcDiscoveryError",
        "OidcJwtAuthenticator",
    }:
        from tide.api.auth import OidcDiscoveryError, OidcJwtAuthenticator

        return {
            "OidcDiscoveryError": OidcDiscoveryError,
            "OidcJwtAuthenticator": OidcJwtAuthenticator,
        }[name]
    if name in {
        "RemoteActionService",
        "RemoteAuditHistoryService",
        "RemoteRecordsService",
        "RemoteReportService",
        "RemoteSecurityView",
    }:
        from tide.api.remote import (
            RemoteActionService,
            RemoteAuditHistoryService,
            RemoteRecordsService,
            RemoteReportService,
            RemoteSecurityView,
        )

        return {
            "RemoteActionService": RemoteActionService,
            "RemoteAuditHistoryService": RemoteAuditHistoryService,
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
