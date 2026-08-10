"""Shared waiting helpers for the Textual suites.

Three copies of `wait_until` lived in `test_tui.py`, `test_studio.py` and
`test_api_remote.py`, identical apart from their failure message, across some
seventy call sites. They agreed, which is what made them easy to leave -- and
is how the field-label transform looked before one copy drifted.
"""

from __future__ import annotations

import time
from typing import Callable

from textual.pilot import Pilot
from textual.widgets import Button

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


async def press_button(
    pilot: Pilot[object],
    selector: str,
    *,
    timeout: float = WAIT_TIMEOUT_SECONDS,
) -> None:
    """Click a button, having first waited until it can receive the click.

    `Button._on_click` returns without pressing while the button still carries
    `-active` -- the class its press animation adds and a timer removes
    `active_effect_duration` (0.2s) later. No message is posted and nothing is
    raised: the press simply does not happen.

    `pilot.pause()` drains the message queue and promises no wall clock at all,
    so two clicks on one button separated by a pause are a coin toss decided by
    how fast the machine is. That is what made
    `test_textual_studio_manages_tabs_and_action_order_in_memory` fail about a
    third of the time: the second of two `#undo-edit` clicks was swallowed, one
    edit stayed on the stack, and the failure surfaced three lines later as
    `assert not app.state.dirty` -- reading as a state bug rather than as a
    lost click.

    Waiting for the class to clear is also the more faithful simulation. A
    person clicking Undo twice takes longer than 200ms; only a test clicks
    again in the same millisecond.
    """

    button = pilot.app.screen.query_one(selector, Button)
    await wait_until(
        pilot,
        lambda: not button.has_class("-active"),
        timeout=timeout,
        description=f"{selector} ready to be pressed again",
    )
    await pilot.click(selector)
