"""TIDE Studio's Textual shell, one module per screen.

This was a single 2,548-line module. It stays importable as `tide.tui.studio`
rather than becoming `studio_app`, `studio_layout` and so on, because callers
already take screens from here and a rearrangement inside is none of their
business.
"""

from __future__ import annotations

from .app import StudioApp
from .groups import StudioGroupEdit, StudioGroupsScreen
from .layout import StudioLayoutEdit, StudioLayoutScreen
from .preview import StudioPreviewScreen
from .save import StudioSaveScreen

__all__ = [
    "StudioApp",
    "StudioGroupEdit",
    "StudioGroupsScreen",
    "StudioLayoutEdit",
    "StudioLayoutScreen",
    "StudioPreviewScreen",
    "StudioSaveScreen",
]
