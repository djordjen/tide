"""Explicit loading of application-owned runtime registrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from tide.compiler.normalized import ApplicationModel

if TYPE_CHECKING:
    from tide.services import ActionService, RecordsService


class ApplicationRuntimeError(ValueError):
    """The application's optional runtime registration is invalid."""


def configure_application_runtime(
    model: ApplicationModel,
    records: RecordsService,
    actions: ActionService,
) -> bool:
    """Run an application's optional ``runtime.py`` registration hook."""

    runtime_file = model.project_root / "runtime.py"
    if not runtime_file.is_file():
        return False
    module = _load_runtime(runtime_file)
    configure = getattr(module, "configure_runtime", None)
    if not callable(configure):
        raise ApplicationRuntimeError(
            "runtime.py must define configure_runtime(records, actions)"
        )
    try:
        configure(records, actions)
    except Exception as error:
        raise ApplicationRuntimeError(
            f"application runtime registration failed: {error}"
        ) from error
    return True


def _load_runtime(runtime_file: Path) -> ModuleType:
    module_name = f"tide_application_runtime_{abs(hash(runtime_file.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, runtime_file)
    if spec is None or spec.loader is None:
        raise ApplicationRuntimeError(
            f"could not load application runtime from {runtime_file.as_posix()}"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise ApplicationRuntimeError(
            f"application runtime failed to load: {error}"
        ) from error
    return module
