"""The shared pilot helpers, each exercised against what it exists for.

`_wait_until` was written out three times -- `test_tui.py`, `test_studio.py`,
`test_api_remote.py` -- identical apart from its failure message, across some
seventy call sites. All three bounded the wait at fifty polls, which is about
half a second of real time: enough on an idle machine, and not enough on a
loaded CI runner draining a worker thread. The Windows job failed on a
web-only commit because of it.

`press_button` arrived later, after a Studio test that failed about a third of
the time was traced to a click Textual had swallowed rather than to the state
bug it resembled. Its two tests below show the trap and the escape
deterministically, instead of one run in three.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from textual.app import App, ComposeResult
from textual.widgets import Button

from textual_support import WAIT_TIMEOUT_SECONDS, press_button, wait_until

ROOT = Path(__file__).parents[1]

# Long enough that no `pilot.pause()` can outlast it, so the swallowed click is
# a certainty rather than the coin toss it is at Textual's 0.2s default.
CERTAIN_WINDOW = 5.0
# Short enough to keep the passing case quick, and far longer than a pause.
BRIEF_WINDOW = 0.5


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


def test_a_second_pilot_click_is_swallowed_while_the_press_animation_runs() -> None:
    """The trap `press_button` exists for, pinned so it cannot change silently.

    `Button._on_click` returns without pressing while `-active` is set, and a
    timer clears that class `active_effect_duration` later. Two `pilot.click`
    calls separated only by `pilot.pause()` therefore deliver one press, with
    nothing raised and no message posted. If a future Textual drops this
    behaviour, this test fails and the helper can go with it.
    """

    app = _CountingApp(CERTAIN_WINDOW)

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#probe")
            await pilot.pause()
            await pilot.click("#probe")
            await pilot.pause()

    asyncio.run(exercise())

    assert app.presses == 1


def test_press_button_waits_for_the_animation_so_every_press_lands() -> None:
    app = _CountingApp(BRIEF_WINDOW)

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await press_button(pilot, "#probe")
            await press_button(pilot, "#probe")
            await press_button(pilot, "#probe")
            await pilot.pause()

    asyncio.run(exercise())

    assert app.presses == 3


class _StubPilot:
    """Enough of a Pilot to drive the helper without an application."""

    def __init__(self) -> None:
        self.pauses = 0

    async def pause(self, delay: float | None = None) -> None:
        del delay
        self.pauses += 1


class _CountingApp(App[None]):
    """One button that records how many presses actually arrived."""

    def __init__(self, active_effect_duration: float) -> None:
        super().__init__()
        self.presses = 0
        self._duration = active_effect_duration

    def compose(self) -> ComposeResult:
        button = Button("Press", id="probe")
        button.active_effect_duration = self._duration
        yield button

    def on_button_pressed(self, event: Button.Pressed) -> None:
        del event
        self.presses += 1
