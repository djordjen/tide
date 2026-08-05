"""Loading an application's YAML and naming positions inside it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeGuard

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from tide.development.designer import (
    PathPart,
)

from .contracts import (
    StudioError,
)




def _yaml() -> YAML:
    loader = YAML(typ="safe", pure=True)
    loader.allow_duplicate_keys = False
    return loader


def load_studio_yaml(file: str, source: str) -> Any:
    try:
        return _yaml().load(source)
    except YAMLError as error:
        raise StudioError(f"cannot display {file}: invalid YAML") from error


def studio_sequence(value: Any) -> TypeGuard[Sequence[Any]]:
    """Say whether a loaded node is a list of things rather than one thing.

    A `TypeGuard` rather than a `bool` so the type checker learns what the
    check means: every caller reads the value straight afterwards, and
    `document.get(...)` hands back `Any | None`.
    """

    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def display_path(path: tuple[PathPart, ...]) -> str:
    if not path:
        return "Document"
    parts: list[str] = []
    for part in path:
        if isinstance(part, int):
            parts[-1] += f"[{part}]"
        else:
            parts.append(part)
    return ".".join(parts)
