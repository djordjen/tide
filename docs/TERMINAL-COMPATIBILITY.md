# Terminal Compatibility Contract

**Status: Initial automated browse/form harness implemented; full release
matrix remains proposed.**

TIDE's terminal adapter must work through keyboard alone; mouse behavior is an
additional route to the same actions. A terminal-specific control must not be
the only way to invoke domain behavior.

## Initial matrix

Automated and manual release checks should cover:

- Windows Terminal on Windows;
- an xterm-compatible terminal on Linux;
- the same Linux application reached through SSH;
- 80x24, 100x30, and a wide desktop viewport;
- UTF-8 content containing narrow, wide, combining, and right-to-left text;
- full color, reduced color, and no-color operation;
- keyboard-only focus traversal and every configured shortcut;
- mouse click, double-click, wheel, and selection behavior where supported.

Layouts declare minimum useful sizes and a deterministic fallback. At widths
below a form's preferred layout, groups stack and nonessential columns collapse;
the application must not become impossible to save or cancel.

## Testable behavior

Renderer tests should assert semantic widget trees and action dispatch rather
than fragile full-screen snapshots alone. A smaller set of golden screenshots
may verify layout at representative dimensions. Width calculations must use
terminal cell width rather than Unicode code-point count.

The initial Textual tests run headlessly at 100x24, 120x30, and 120x40. They
exercise metadata columns, secured reference display, key-driven paging and
refresh, clickable navigation, metadata forms, typed reference selectors,
master-detail editing, create/save/post actions, immutable states, and stale
commit feedback. Browse coverage also includes incremental search, metadata
filters, query-reset behavior, and ascending/descending sort controls. This is
the automated floor, not certification of the wider terminal matrix. Form
controls use single-row compact rendering; editable versus read-only state is
communicated through both color and italic styling, rather than color alone.
Form focus tests require column-first traversal (left top-to-bottom, then right
top-to-bottom), Enter-to-advance behavior, and normal keyboard operation inside
selection overlays. Reference lookup coverage includes modal multi-column
tables, incremental case-insensitive search, keyboard selection, and
selection-driven draft values. Inline-editor coverage also verifies that an
explicit two-column layout controls widget placement and focus order without
changing the collection table columns.

SSH tests should include noticeable latency and interrupted connections.
`RecordSession` retains unsaved edits until the user explicitly cancels or the
session is irrecoverably closed; reconnect behavior must never duplicate an
action silently.
