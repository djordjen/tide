# Terminal Compatibility Contract

**Status: Automated compact/standard/wide browse and form acceptance is
implemented; cross-terminal, color-depth, and SSH certification remain.**

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

The Textual acceptance tests now run the same browse and invoice form at
80x24, 100x30, and 140x40. They assert that every browse action and every
Cancel/Save/Post form action stays inside the viewport. Below 100 columns,
nonessential workspace/filter/sort selectors collapse, the browse actions use
a compact bar, and the form body scrolls independently so line details remain
reachable while record actions stay fixed. A compact browse regression also
round-trips mixed wide CJK, combining-accent, and right-to-left text.

The broader headless suite also exercises metadata columns, secured reference
display, key-driven paging and refresh, clickable navigation, metadata forms,
typed reference selectors,
master-detail editing, create/save/post actions, immutable states, and stale
commit feedback. Browse coverage also includes incremental search, metadata
filters, query-reset behavior, and ascending/descending sort controls. Secured
Customer/Product deletion coverage verifies metadata/permission visibility,
Delete-key and clickable activation, a safe-default confirmation modal,
cancellation, successful refresh, readable reference-conflict feedback, and
the same behavior through the remote HTTP facade at compact and standard sizes.
Stale-edit coverage exercises the three-way conflict table, safe-default draft
retention, current-record reload, safe-field rebasing, selected-row **Use
Current**/**Use Mine** choices, mixed-field resolutions, remote values, and the
case where a concurrent workflow transition makes a formerly editable field
read-only. Apply remains disabled until every overlap has a choice. The conflict
dialog and all resolution controls are exercised at `80x24` as well as standard
sizes. This is the automated floor, not certification of the wider terminal
matrix. Form controls use single-row compact rendering; editable versus
read-only state is
communicated through both color and italic styling, rather than color alone.
Form focus tests require column-first traversal (left top-to-bottom, then right
top-to-bottom), Enter-to-advance behavior, and normal keyboard operation inside
selection overlays. Reference lookup coverage includes modal multi-column
tables, incremental case-insensitive search, keyboard selection, and
selection-driven draft values. Inline-editor coverage also verifies that an
explicit two-column layout controls widget placement and focus order without
changing the collection table columns. Browse, inline collection, and lookup
tables derive alignment from field types: integer and decimal columns are
right-aligned, while other field types remain left-aligned.
Numeric edit-mask coverage verifies that invalid extra decimal digits are
blocked during entry and fixed decimal places are completed on focus loss.
Regular-expression masks validate completed string values and remain backed by
the same service-side rule used by non-terminal adapters.
Invoice report coverage opens the selected record with **Preview** or `V`,
checks the secured document contents, exports HTML and PDF, and closes without
altering the browse or record session. Report data, formatting, and export are
owned by headless services/renderers rather than reconstructed by Textual.
Windows Terminal, xterm/SSH, reduced-color/no-color, latency, wheel, and
reconnect cases remain manual release certification until their harnesses are
implemented.

SSH tests should include noticeable latency and interrupted connections.
`RecordSession` retains unsaved edits until the user explicitly cancels or the
session is irrecoverably closed; reconnect behavior must never duplicate an
action silently.
