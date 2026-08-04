"""Explicit opt-in loading for application-owned demo records."""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from tide.compiler.normalized import ApplicationModel
from tide.data.repository import Repository


class DemoDataError(ValueError):
    """The application's optional demo-data provider is missing or invalid."""


def seed_demo_data(
    model: ApplicationModel,
    repository: Repository,
) -> int:
    """Load and seed ``demo_data.py`` from an explicitly run application."""

    provider_file = model.project_root / "demo_data.py"
    if not provider_file.is_file():
        raise DemoDataError(
            f"demo data provider was not found: {provider_file.as_posix()}"
        )
    module = _load_provider(provider_file)
    provider = getattr(module, "load_demo_data", None)
    if not callable(provider):
        raise DemoDataError("demo_data.py must define load_demo_data()")
    data = provider()
    if not isinstance(data, Mapping):
        raise DemoDataError("load_demo_data() must return an entity mapping")

    seeded = 0
    for entity_name, raw_records in data.items():
        if not isinstance(entity_name, str) or entity_name not in model.entities:
            raise DemoDataError(f"unknown demo-data entity: {entity_name!r}")
        if not isinstance(raw_records, (list, tuple)):
            raise DemoDataError(
                f"demo records for {entity_name} must be a list or tuple"
            )
        records: list[dict[str, Any]] = []
        for index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, Mapping):
                raise DemoDataError(
                    f"demo record {entity_name}[{index}] must be a mapping"
                )
            records.append(dict(raw_record))
        repository.seed(entity_name, records)
        seeded += len(records)
    _reserve_sequence_floors(module, repository)
    return seeded


def _reserve_sequence_floors(module: ModuleType, repository: Repository) -> None:
    """Raise sequence floors past whatever the demo data already occupies.

    Seeding puts rows in without allocating, so a sequence that starts at 1
    would reissue numbers the seeded rows are already showing. Only the
    application can say how far they reach: the stored value is whatever its
    generator rendered -- `INV-2026-0001` here -- and no framework can invert
    an arbitrary formatter to recover the number behind it.

    Optional, because plenty of demo data has nothing generated in it.
    """

    floors = getattr(module, "load_sequence_floors", None)
    if floors is None:
        return
    if not callable(floors):
        raise DemoDataError("load_sequence_floors must be callable")
    reserved = floors()
    if not isinstance(reserved, Mapping):
        raise DemoDataError("load_sequence_floors() must return a name mapping")
    for name, value in reserved.items():
        if not isinstance(name, str) or not name:
            raise DemoDataError("demo sequence names must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DemoDataError(
                f"demo sequence floor for {name!r} must be a non-negative integer"
            )
        repository.reserve_sequence_value(name, value)


def _load_provider(provider_file: Path) -> ModuleType:
    module_name = f"tide_demo_data_{abs(hash(provider_file.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, provider_file)
    if spec is None or spec.loader is None:
        raise DemoDataError(f"could not load demo data from {provider_file.as_posix()}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise DemoDataError(f"demo data provider failed: {error}") from error
    return module
