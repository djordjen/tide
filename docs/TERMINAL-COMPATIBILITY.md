# Terminal Compatibility Contract

**Status: Proposed test contract.**

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

SSH tests should include noticeable latency and interrupted connections.
`RecordSession` retains unsaved edits until the user explicitly cancels or the
session is irrecoverably closed; reconnect behavior must never duplicate an
action silently.

