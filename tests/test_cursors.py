from __future__ import annotations

import pytest

from tide.data import FilterCondition, SortField
from tide.runtime import InvalidQueryCursor
from tide.services.cursors import (
    CURSOR_VERSION,
    CursorShape,
    CursorState,
    InMemoryCursorStore,
)


def cursor_state(*, value: object = 1) -> CursorState:
    return CursorState(
        version=CURSOR_VERSION,
        shape=CursorShape(
            model=("Test", "0.1.0", "0.1"),
            entity="crm.Customer",
            filters=(FilterCondition("active", "eq", True),),
            sort=(SortField("name"), SortField("id")),
            limit=25,
            principal=("user:1", ("crm.customer.read",)),
        ),
        values=("Sensitive customer name", value),
    )


def test_cursor_tokens_are_opaque_and_resolve_to_typed_server_state() -> None:
    store = InMemoryCursorStore()
    state = cursor_state()

    token = store.issue(state)

    assert len(token) >= 40
    assert "Sensitive" not in token
    assert store.resolve(token) == state


def test_cursor_store_expires_and_bounds_entries() -> None:
    now = [100.0]
    tokens = iter(("token-1", "token-2", "token-3"))
    store = InMemoryCursorStore(
        ttl_seconds=10,
        max_entries=2,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )

    first = store.issue(cursor_state(value=1))
    second = store.issue(cursor_state(value=2))
    third = store.issue(cursor_state(value=3))

    with pytest.raises(InvalidQueryCursor):
        store.resolve(first)
    assert store.resolve(second).values[-1] == 2
    assert store.resolve(third).values[-1] == 3

    now[0] = 110.0
    with pytest.raises(InvalidQueryCursor, match="invalid or expired"):
        store.resolve(third)


@pytest.mark.parametrize("token", ["", "unknown", None])
def test_cursor_store_rejects_unknown_or_invalid_tokens(token: object) -> None:
    store = InMemoryCursorStore()

    with pytest.raises(InvalidQueryCursor):
        store.resolve(token)  # type: ignore[arg-type]
