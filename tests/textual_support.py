"""Shared waiting helper for the Textual suites.

Three copies of this lived in `test_tui.py`, `test_studio.py` and
`test_api_remote.py`, identical apart from their failure message, across some
seventy call sites. They agreed, which is what made them easy to leave -- and
is how the field-label transform looked before one copy drifted.
"""

from __future__ import annotations

import time
from typing import Callable

from textual.pilot import Pilot

WAIT_TIMEOUT_SECONDS = 10.0
"""How long a Textual state may take before the wait is called a failure.

Deliberately generous. The old bound was fifty polls, which sounds patient and
is about half a second of real time: enough on an idle machine, and not enough
on a loaded CI runner draining a worker thread, so the suite failed for being
slow rather than for being wrong. A wait that is too tight reports a defect
that is not there; one that is too loose only makes a real failure slower to
arrive, and a real failure is not on the critical path.
"""


async def wait_until(
    pilot: Pilot[object],
    condition: Callable[[], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SECONDS,
    description: str = "the expected state",
) -> None:
    """Drain Textual messages until an observable state is reached.

    The condition is checked twice with a pause between, because Textual can
    pass through the expected state on its way somewhere else: a table that
    briefly holds the right row count while it is still being rebuilt would
    otherwise satisfy a single check and let the assertion after it race.
    """

    deadline = time.monotonic() + timeout
    while True:
        await pilot.pause()
        if condition():
            await pilot.pause()
            if condition():
                return
        if time.monotonic() >= deadline:
            break
        await pilot.pause(0.01)
    assert condition(), f"Textual did not reach {description}"
