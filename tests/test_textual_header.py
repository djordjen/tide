"""The header has to survive being asked for a title it cannot draw yet.

CI failed once, on Windows only, with

    textual.css.query.NoMatches: No nodes match 'HeaderTitle' on Header()

raised from `textual.widgets._header.set_title`, which is the callback
`Header._on_mount` registers against four reactives -- `App.title`,
`App.sub_title`, `Screen.title` and `Screen.sub_title` -- and which catches
only `NoScreen`.

Those watchers are registered with `init=True`, so the first call is scheduled
at mount; the widgets `Header.compose` yields are mounted separately and
afterwards. Between the two the header is in the DOM with no `HeaderTitle`
under it, and anything that touches a title in that window takes the whole
application down. A loaded runner is what widens the window; nothing about it
is remote.

These tests make that state deliberately rather than waiting for a slow
machine to make it, and they assert the other half too -- that a header which
tolerated the absence still shows the title once there is something to show
it in. A guard that swallowed the update forever would satisfy the first test
and leave every screen headed by a blank bar.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re

from textual.app import App, ComposeResult
from textual.events import Mount
from textual.widgets import Static
from textual.widgets._header import HeaderTitle

import tide.tui
from tide.tui.header import TideHeader


class _Probe(App[None]):
    TITLE = "Probe application"
    SUB_TITLE = "a subtitle"

    def compose(self) -> ComposeResult:
        yield TideHeader(show_clock=False)
        yield Static("body", id="body")


def test_a_title_change_with_no_title_widget_does_not_end_the_application() -> None:
    app = _Probe()

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one(TideHeader)
            # The state a slow runner reaches by ordering, reached here by
            # subtraction: the header is mounted, and the widget its watcher
            # queries is not there.
            await header.query_one(HeaderTitle).remove()

            app.title = "a different title"
            app.sub_title = "a different subtitle"
            await pilot.pause()
            await pilot.pause()

    asyncio.run(exercise())


def test_the_header_still_shows_the_title_it_was_given() -> None:
    """The half a swallowed exception would quietly cost."""

    app = _Probe()

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            title = app.query_one(HeaderTitle)
            assert "Probe application" in str(title.render())

            app.title = "Renamed application"
            await pilot.pause()
            assert "Renamed application" in str(title.render())

    asyncio.run(exercise())


class _Unwatched(TideHeader):
    """A header with no title watchers at all.

    `prevent_default` stops the base classes' handlers, and this one registers
    nothing in their place -- so whatever title this header shows was put
    there by `compose` and by nothing else.
    """

    def _on_mount(self, event: Mount) -> None:
        event.prevent_default()


class _UnwatchedProbe(App[None]):
    TITLE = "Probe application"
    SUB_TITLE = "a subtitle"

    def compose(self) -> ComposeResult:
        yield _Unwatched(show_clock=False)
        yield Static("body", id="body")


def test_the_title_is_composed_in_rather_than_watched_in() -> None:
    """Tolerating the absence is not enough on its own.

    On a screen whose title is set once and never changed, the watcher's first
    call is also its only call -- so a guard that swallowed it would leave the
    bar blank for the life of that screen, and every test above would still
    pass. The title is put in as the widget is made instead, which is what
    makes swallowing safe.
    """

    app = _UnwatchedProbe()

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            title = app.query_one(HeaderTitle)

            assert "Probe application" in str(title.render())

    asyncio.run(exercise())


def test_no_terminal_screen_reaches_past_it_to_textuals_own_header() -> None:
    """Derived rather than listed, because a list of call sites drifts.

    Five screens compose a header. A sixth that imported Textual's directly
    would be one screen that still ends the application on a slow machine, and
    nothing else here would notice.
    """

    # `Header` as a whole word: `TideHeader` and `HeaderTitle` are not it.
    single_line = re.compile(r"^from textual\.widgets import .*\bHeader\b", re.M)
    inside_parentheses = re.compile(r"^\s+Header,\s*$", re.M)

    tui = Path(tide.tui.__file__).parent
    scanned = 0
    offenders = []
    for module in sorted(tui.rglob("*.py")):
        if module.name == "header.py":
            continue
        scanned += 1
        source = module.read_text(encoding="utf-8")
        if single_line.search(source) or inside_parentheses.search(source):
            offenders.append(module.name)

    # A scan that reached nothing would agree with a clean tree. The first
    # version of this test matched nothing at all -- a `\b` written through a
    # shell heredoc arrived as a literal backspace -- and passed with an
    # offending import sitting in front of it.
    assert scanned > 10, f"only {scanned} terminal modules were read"
    assert offenders == [], (
        f"{offenders} import Textual's Header; use tide.tui.header.TideHeader"
    )

