# Renderer Acceptance Matrix

**Status: executable parity baseline implemented.**

TIDE Framework has one compiled application model and one service/security
boundary, but Textual, Qt, and Web remain different presentation technologies.
The renderer acceptance matrix makes the shared behavior explicit without
requiring the widgets to look identical.

The versioned source is
[`renderer-acceptance.yaml`](renderer-acceptance.yaml). CI validates that:

- every capability has an explicit Textual, Qt, and Web status;
- every **covered** cell points to a real automated test;
- all **parity** capabilities are covered by all three renderers;
- the golden Invoicing navigation, browse columns/alignment, and form
  sections still match the compiled `ApplicationModel`; and
- the authenticated Web presentation projection preserves that golden
  renderer-neutral contract.

Run the executable matrix checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_renderer_acceptance.py -q
```

The referenced evidence then runs through the normal Python and Web CI jobs.
Tests marked `python-gui` require the optional PySide6 GUI dependency; the same
semantic presenter behavior also has headless Qt coverage where practical.

## Current parity baseline

The required baseline is green for all three renderers:

| Shared behavior | Textual | Qt | Web |
|---|---:|---:|---:|
| Capability-filtered application navigation | Covered | Covered | Covered |
| Opaque-cursor continuation | Covered | Covered | Covered |
| Shared form rows, groups, and collections | Covered | Covered | Covered |
| Workflow-aware editable/read-only state | Covered | Covered | Covered |
| Flat Customer/Product create and update | Covered | Covered | Covered |
| Multi-column lookup and Save & Select | Covered | Covered | Covered |
| Exact formatting and numeric alignment | Covered | Covered | Covered |
| Keyboard form and record navigation | Covered | Covered | Covered |

“Covered” means the behavior has automated evidence, not that every renderer
uses the same control or pixel layout. Renderer-specific measurements,
virtualization, native dialogs, and terminal responsiveness remain idiomatic to
their surfaces.

## Deliberate gaps

The same matrix records non-parity work instead of hiding it:

- personal column order/width is implemented in Qt and Web; direct drag
  resizing is not a terminal interaction;
- lookup and nested **Save & Select** are implemented in all three renderers;
- Invoice master-detail drafts, three-way stale-conflict review, domain
  actions, and report preview/export are implemented in Textual and Qt but
  remain planned for Web.

Changing a renderer status to **covered** requires adding executable evidence
in the same change. If application YAML changes the shared layout, the golden
contract must be deliberately reviewed and updated rather than silently
drifting in one renderer.
