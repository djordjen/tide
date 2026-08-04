"""One waiting helper, with a bound that says what it means.

`_wait_until` was written out three times -- `test_tui.py`, `test_studio.py`,
`test_api_remote.py` -- identical apart from its failure message, across some
seventy call sites. All three bounded the wait at fifty polls, which is about
half a second of real time: enough on an idle machine, and not enough on a
loaded CI runner draining a worker thread. The Windows job failed on a
web-only commit because of it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from textual_support import WAIT_TIMEOUT_SECONDS, wait_until

ROOT = Path(__file__).parents[1]


def test_no_suite_keeps_its_own_polling_loop() -> None:
    """The duplication is the defect, so the guard is against its shape."""

    pattern = "for _ in range(" + "attempts)"
    offenders = sorted(
        path.name
        for path in (ROOT / "tests").glob("test_*.py")
        if path.name != Path(__file__).name
        and pattern in path.read_text(encoding="utf-8")
    )

    assert offenders == []


def test_the_bound_is_time_and_not_a_poll_count() -> None:
    """Fifty polls reads as patient and is half a second.

    Stating it in seconds is what makes it reviewable: nobody can tell whether
    fifty polls is long enough without knowing what a poll costs.
    """

    assert WAIT_TIMEOUT_SECONDS >= 5.0


def test_a_condition_that_never_holds_is_a_failure() -> None:
    app = _StubPilot()

    with pytest.raises(AssertionError, match="did not reach"):
        asyncio.run(wait_until(app, lambda: False, timeout=0.05))


def test_a_condition_that_holds_returns_without_waiting_it_out() -> None:
    app = _StubPilot()

    asyncio.run(wait_until(app, lambda: True, timeout=30.0))

    assert app.pauses == 2, "checked twice, because Textual passes through states"


class _StubPilot:
    """Enough of a Pilot to drive the helper without an application."""

    def __init__(self) -> None:
        self.pauses = 0

    async def pause(self, delay: float | None = None) -> None:
        del delay
        self.pauses += 1
