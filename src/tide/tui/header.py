"""The application header, minus a race that takes the whole app down.

Textual's `Header` registers four watchers when it mounts -- on `App.title`,
`App.sub_title`, `Screen.title` and `Screen.sub_title` -- and their callback
does `self.query_one(HeaderTitle)`, catching only `NoScreen`. The widgets
`Header.compose` yields are mounted separately, and afterwards, so between the
header's own mount and its children's there is a window in which that query
has nothing to find. The watchers are registered with `init=True`, which
schedules the first call inside that window; normally it is processed after
the children arrive, and under load it is not.

When it is not, `NoMatches` leaves the callback, leaves the message pump, and
ends the application. It cost one CI run on the Windows job -- for a race
nothing in the terminal client causes and nothing in it could prevent.

Two changes, because the obvious one alone is a different bug. **The title is
composed in**, so it is right before any watcher runs and a swallowed update
cannot leave a bar blank: on a screen whose title never changes, the watcher's
first call is also its only call. And **the watcher tolerates the absence**,
so the window is survivable rather than fatal.

Deliberately not a retry loop. The first version of this counted attempts and
rescheduled with `call_after_refresh`, which spends its whole budget inside a
single `pilot.pause()` -- measured, four attempts before the child arrived --
so the bound was on the wrong axis and the fix did nothing. Making it wait on
state instead would be a busy loop against a header that never composes.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.dom import NoScreen
from textual.events import Mount
from textual.widgets import Header
from textual.widgets._header import HeaderTitle


class TideHeader(Header):
    """`Header`, tolerant of being asked for a title it cannot draw yet."""

    def compose(self) -> ComposeResult:
        """Textual's own children, with the title already in the title widget.

        `Header.compose` yields an empty `HeaderTitle` and leaves filling it
        to the watcher. Filling it here removes the window rather than
        surviving it: by the time anything can ask, the answer is already
        there.
        """

        for widget in super().compose():
            if isinstance(widget, HeaderTitle):
                try:
                    widget.update(self.format_title())
                except NoScreen:
                    pass
            yield widget

    def _on_mount(self, event: Mount) -> None:
        # Textual dispatches a handler from *every* class in the MRO, so an
        # override sits beside the base method rather than replacing it: the
        # first attempt at this registered a safe watcher next to the unsafe
        # one and the traceback still came from `_header.py`. This is the
        # documented way to say "not that one", and it replaces the whole of
        # `Header._on_mount`, which registers these four watchers and does
        # nothing else.
        event.prevent_default()

        async def set_title() -> None:
            try:
                self.query_one(HeaderTitle).update(self.format_title())
            except NoScreen:
                # Textual's own guard: no screen, no title to speak of.
                pass
            except NoMatches:
                # Mounted, not yet composed. There is nothing to update, and
                # nothing is lost: `compose` puts the current title in as the
                # widget is made.
                pass

        self.watch(self.app, "title", set_title)
        self.watch(self.app, "sub_title", set_title)
        self.watch(self.screen, "title", set_title)
        self.watch(self.screen, "sub_title", set_title)
