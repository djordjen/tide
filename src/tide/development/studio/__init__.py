"""TIDE Studio's in-memory editing session.

This was a single 2,608-line module: the contracts callers receive, the
service that builds them, and about fifty helpers doing the building. It
stays importable as `tide.development.studio`, which is where every
renderer and `tide.development` itself already take these names from.
"""

from __future__ import annotations

from .contracts import (
    StudioDocumentDetails,
    StudioDocumentGroup,
    StudioDocumentNode,
    StudioError,
    StudioGroupKind,
    StudioModel,
    StudioPreviewAccess,
    StudioPreviewAction,
    StudioPreviewField,
    StudioProperty,
    StudioSaveResult,
    StudioSaveReview,
    StudioSessionState,
    StudioViewAvailableCollection,
    StudioViewAvailableField,
    StudioViewField,
    StudioViewGroup,
    StudioViewPreview,
    StudioViewSection,
    StudioViewStructure,
    StudioViewTrack,
    StudioWorkspace,
)
from .service import StudioService

__all__ = [
    "StudioDocumentDetails",
    "StudioDocumentGroup",
    "StudioDocumentNode",
    "StudioError",
    "StudioGroupKind",
    "StudioModel",
    "StudioPreviewAccess",
    "StudioPreviewAction",
    "StudioPreviewField",
    "StudioProperty",
    "StudioSaveResult",
    "StudioSaveReview",
    "StudioService",
    "StudioSessionState",
    "StudioViewAvailableCollection",
    "StudioViewAvailableField",
    "StudioViewField",
    "StudioViewGroup",
    "StudioViewPreview",
    "StudioViewSection",
    "StudioViewStructure",
    "StudioViewTrack",
    "StudioWorkspace",
]
