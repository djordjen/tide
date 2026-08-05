"""The Qt renderer's reference-display cache.

It came out of `QtBrowseController`, where it was the only mutable state and
the only lock. A browse grid asks for the same customer once per row that
names it, and Qt resolves references on worker threads -- so "how many
requests did that take" and "what did the second thread see" are the
behaviours worth pinning, and neither was covered while it lived inside a
1,677-line class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import pytest

from tide.compiler import compile_project
from tide.qt.references import ReferenceDisplayCache
from tide.runtime import TideRuntimeError

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@dataclass
class _Record:
    values: dict[str, Any]


class _CountingClient:
    """Answers `get_record`, counting how often it was actually asked."""

    def __init__(self, *, barrier: Barrier | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._barrier = barrier

    def get_record(self, entity_name: str, identity: Any) -> _Record:
        self.calls.append((entity_name, identity))
        if self._barrier is not None:
            # Both threads arrive before either returns, so a cache that
            # checked once and wrote blindly would let the second overwrite.
            self._barrier.wait(timeout=5)
        return _Record(
            {"id": identity, "code": f"C{identity}", "name": f"Customer {identity}"}
        )


class _RefusingClient:
    def get_record(self, entity_name: str, identity: Any) -> _Record:
        raise TideRuntimeError("not permitted")


@pytest.fixture(scope="module")
def model() -> Any:
    return compile_project(INVOICING)


def test_a_repeated_reference_is_fetched_once(model: Any) -> None:
    client = _CountingClient()
    cache = ReferenceDisplayCache(client, model)

    first = cache.display("crm.Customer", 1)
    second = cache.display("crm.Customer", 1)

    assert first == second == "C1 - Customer 1"
    assert client.calls == [("crm.Customer", 1)]


def test_two_threads_racing_for_one_record_agree(model: Any) -> None:
    """Both may ask, but they must not disagree about the answer."""

    client = _CountingClient(barrier=Barrier(2))
    cache = ReferenceDisplayCache(client, model)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in [
                pool.submit(cache.display, "crm.Customer", 7),
                pool.submit(cache.display, "crm.Customer", 7),
            ]
        ]

    assert results == ["C7 - Customer 7", "C7 - Customer 7"]
    assert cache.display("crm.Customer", 7) == "C7 - Customer 7"


def test_a_remembered_display_is_never_fetched(model: Any) -> None:
    client = _CountingClient()
    cache = ReferenceDisplayCache(client, model)

    cache.remember("crm.Customer", 3, "ALREADY - Known")

    assert cache.display("crm.Customer", 3) == "ALREADY - Known"
    assert client.calls == []


def test_clearing_makes_it_ask_again(model: Any) -> None:
    client = _CountingClient()
    cache = ReferenceDisplayCache(client, model)

    cache.display("crm.Customer", 2)
    cache.clear()
    cache.display("crm.Customer", 2)

    assert client.calls == [("crm.Customer", 2), ("crm.Customer", 2)]


def test_a_record_the_caller_may_not_read_says_so(model: Any) -> None:
    """It is drawn beside data they can see, so it reads rather than raises."""

    cache = ReferenceDisplayCache(_RefusingClient(), model)

    assert cache.display("crm.Customer", 4) == "Protected"
