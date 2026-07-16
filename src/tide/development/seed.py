"""Explicit application-owned Faker seeding for development databases."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Mapping

from tide.compiler.normalized import ApplicationModel
from tide.runtime import RequestContext
from tide.services import ActionService, RecordsService


class FakeDataError(ValueError):
    """The optional Faker dependency or application provider is unavailable."""


def seed_fake_data(
    model: ApplicationModel,
    records: RecordsService,
    actions: ActionService,
    context: RequestContext,
    *,
    counts: Mapping[str, int],
    random_seed: int,
    locale: str,
) -> Mapping[str, int]:
    """Run the selected application's trusted fake-data provider."""
    try:
        from faker import Faker
    except ModuleNotFoundError as error:
        raise FakeDataError(
            "Faker is not installed; install tide-framework[seed]"
        ) from error

    provider_file = model.project_root / "fake_data.py"
    if not provider_file.is_file():
        raise FakeDataError(
            f"fake-data provider was not found: {provider_file.as_posix()}"
        )
    module = _load_provider(provider_file)
    provider = getattr(module, "seed_fake_data", None)
    if not callable(provider):
        raise FakeDataError("fake_data.py must define seed_fake_data()")

    faker = Faker(locale)
    faker.seed_instance(random_seed)
    result = provider(
        faker=faker,
        records=records,
        actions=actions,
        context=context,
        counts=dict(counts),
        random_seed=random_seed,
    )
    if not isinstance(result, Mapping) or any(
        not isinstance(name, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for name, count in result.items()
    ):
        raise FakeDataError(
            "seed_fake_data() must return a mapping of names to non-negative counts"
        )
    return dict(result)


def _load_provider(provider_file: Path) -> ModuleType:
    module_name = f"tide_fake_data_{abs(hash(provider_file.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, provider_file)
    if spec is None or spec.loader is None:
        raise FakeDataError(
            f"could not load fake data from {provider_file.as_posix()}"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise FakeDataError(f"fake-data provider failed: {error}") from error
    return module
