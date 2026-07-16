"""Adapter-independent API contracts with optional FastAPI hosting."""

from __future__ import annotations

from typing import Any

from tide.api.openapi import OpenApiPreview, build_openapi_preview, generate_openapi

__all__ = [
    "BearerAuthenticator",
    "DevelopmentTokenAuthenticator",
    "OpenApiPreview",
    "build_fastapi_app",
    "build_openapi_preview",
    "generate_openapi",
]


def __getattr__(name: str) -> Any:
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
