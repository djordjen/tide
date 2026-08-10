# Renderer Acceptance Matrix

**Status: executable parity baseline implemented.**

TIDE Framework has one compiled application model and one service/security
boundary, but Textual and Web remain different presentation technologies.
The renderer acceptance matrix makes the shared behavior explicit without
requiring the widgets to look identical.

The versioned source is
[`renderer-acceptance.yaml`](renderer-acceptance.yaml). CI validates that:

- every capability has an explicit Textual and Web status;
- every **covered** cell points to a real automated test;
- all **parity** capabilities are covered by both renderers; and
- each renderer, asked through **its own** entry point, resolves the golden
  Invoicing navigation, browse columns/alignment, and form sections recorded in
  the matrix.

That last check is the one that catches drift. It does not read the shared
`tide.presentation` helpers and call the answer parity: it builds a `TideApp`
and a `RecordEditScreen` for the TUI, and a
presentation manifest for the Web, then compares what each one resolved. A
renderer that quietly forks its own layout resolution stops matching, which is
how three separate label helpers and two computed-field previews were able to
diverge unnoticed before.

Run the executable matrix checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_renderer_acceptance.py -q
```

### What the checks can and cannot see

Evidence for a `python` runner is resolved by **importing** the module and
looking the test function up on it. A regex over the source proves
only that the characters are present: a module that no longer imports, a test
that moved inside a class, and a `def` sitting in a docstring all read the same
to a text search, and none of them can be run.

One limit is deliberate and recorded rather than hidden: evidence for the `web`
runner stays a source match. Vitest owns those tests and the Python suite
cannot ask it what ran, so this is the one evidence class the matrix still
takes on trust.

Every check above is itself exercised with deliberately broken input — a cell
claiming coverage without evidence, a regressed parity status, a renamed
selector, a renderer whose columns drift — because a guarantee nobody has
watched fail is not a guarantee.

## Current parity baseline

The required baseline is green for both renderers:

| Shared behavior | Textual | Web |
|---|---:|---:|
| Capability-filtered application navigation | Covered | Covered |
| Opaque-cursor continuation | Covered | Covered |
| Shared form rows, groups, and collections | Covered | Covered |
| Workflow-aware editable/read-only state | Covered | Covered |
| Flat Customer/Product create and update | Covered | Covered |
| Multi-column lookup and Save & Select | Covered | Covered |
| Transactional Invoice master-detail draft | Covered | Covered |
| Metadata-driven domain actions | Covered | Covered |
| Secured report preview and export | Covered | Covered |
| Exact formatting and numeric alignment | Covered | Covered |
| Keyboard form and record navigation | Covered | Covered |

“Covered” means the behavior has automated evidence, not that every renderer
uses the same control or pixel layout. Renderer-specific measurements,
virtualization, native dialogs, and terminal responsiveness remain idiomatic to
their surfaces.

## Deliberate gaps

The same matrix records non-parity work instead of hiding it:

- personal column order/width is implemented in Web; direct drag
  resizing is not a terminal interaction;
- lookup and nested **Save & Select** are implemented in both renderers;
- Invoice master-detail drafts are implemented in both renderers;
- three-way stale-conflict review is implemented in both renderers;
- domain actions are implemented in both renderers;
- report preview/export is implemented in both renderers.

Changing a renderer status to **covered** requires adding executable evidence
in the same change. If application YAML changes the shared layout, the golden
contract must be deliberately reviewed and updated rather than silently
drifting in one renderer.
