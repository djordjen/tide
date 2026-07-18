# Designers and Reporting

## Principle

Designers edit metadata and overlays, not generated source code. A preview uses
the same compiler and renderer as the deployed application.

Generated browses, forms, lookups, and basic reports must work before visual
designers exist. Building designers after metadata contracts stabilize avoids
embedding early model mistakes in a large tooling codebase.

## Headless DesignerService

TIDE Studio is built around a UI-independent command service:

```text
Textual designer ----+
                     +--> DesignerService --> model compiler --> YAML overlays
Web designer --------+
                     |
Developer MCP -------+

Local approval host ----> DesignerSaveService --> approved YAML replacement
```

The implemented first command vocabulary is:

```text
set_value
remove_value
rename_key
reorder_mapping
insert_sequence_item
move_sequence_item
replace_document_source
undo
redo
```

Commands address project, entity, view, and report documents by semantic
identity, with a canonical existing-source reference for security,
presentation, and other YAML documents. A bounded atomic batch forms one undo
entry, allowing a field rename and its dependent view/reference changes to be
validated together. Mapping order and sequence commands preserve the authoring
order required by forms and layouts.

`replace_document_source` is the bounded expert-editor command. It accepts one
existing YAML document, rejects malformed or duplicate-key YAML, enforces the
same total-source byte limit, and preserves the document's semantic identity
(`schema_version`, entity, view, or report name). It therefore cannot turn a
single-file source edit into an unsafe partial rename.

`DesignerService` now opens a process-local working tree, applies these typed
commands only in memory, recompiles the exact candidate in a temporary
directory, and returns compiler diagnostics, fingerprints, changed files and a
unified source diff. Invalid intermediate states remain visible and undoable.
Undo/redo history, command batches, path depth, source count, and source bytes
are bounded. No command runs application Python, opens the configured database,
or writes an application source file.

Round-trip YAML editing preserves comments, quotes, flow/block style and key
order so a one-property command does not replace an entire human-authored file.

### Approved save boundary

`DesignerSaveService.prepare()` is a no-write operation. It re-evaluates the
current session, rereads the live application, refuses an invalid/no-op/stale
candidate, and accepts replacements only when the source inventory is unchanged
and every changed file is YAML. Its deterministic approval challenge binds:

- the canonical application path and project file;
- the exact original source-tree fingerprint;
- the candidate ID and complete candidate fingerprint;
- each changed file's before/after hash and byte count;
- the exact unified-diff hash.

The interactive `tide designer save APPLICATION CHANGES.json` command has no
non-interactive confirmation switch. After the user types the complete `SAVE
tide-designer-approval-...` challenge, save reacquires and validates all bound
values, takes an exclusive application lock, stages the complete bounded source
tree, verifies its bytes and compiles it again. It then rechecks the live tree
and each affected YAML digest immediately before mutation.

Changed files use same-filesystem atomic replacement under that lock. Originals
are kept in the private sibling staging directory until all replacements and
the `.tide/designer/<approval-id>.json` receipt succeed. A normal failure rolls
the changed set back in reverse order and removes TIDE-owned temporary state.
If rollback itself is incomplete, TIDE deliberately preserves the lock and
recovery directory instead of hiding a partial result.

### Interrupted-save recovery

Every save now creates a structured lock record before staging and holds a
cross-platform operating-system byte lock until it finishes. Candidate files
are flushed before mutation. An atomically replaced, fsynced
`transaction.json` in the sibling stage records the transaction identity,
project/candidate/artifact hashes, receipt path, completed files, active file,
and whether its backup has moved. A process interruption releases the OS lock
but leaves the structured lock and stage discoverable.

`tide designer recover APPLICATION --preview` acquires that OS lock and performs
no writes. It validates canonical paths and identifiers before following them,
then derives the only safe action from actual bytes:

- **rollback** when every changed target is still at its base, missing with a
  verified base backup, or contains the verified candidate with its base backup;
- **finalize** only when the exact save receipt exists and the complete live
  tree matches the candidate fingerprint;
- **refuse** for active saves, unrelated source drift, malformed records,
  symlinks, unexpected hashes, missing required backups, or mixed evidence.

Interactive recovery requires the exact evidence-bound `RECOVER
tide-designer-recovery-...` challenge. Rollback is reverse ordered and
idempotent, so another interruption can be previewed and resumed. Recovery
recompiles and fingerprints the complete restored/finalized tree before deleting
the stage and lock. It never rolls a receipted candidate back merely because
cleanup was interrupted, and it never guesses how to resolve corrupted evidence.

## Textual Studio shell

The first visible Studio adapter is executable with:

```powershell
uv run --extra studio tide studio applications/invoicing
```

On Windows, `start.bat studio` opens the same screen. This Studio process is
separate from `tide run`: it is developer tooling for application metadata,
not the generated business application.

The Textual shell shows a semantic Application, Entities, Views and Reports
tree plus every YAML source file. Moving the tree selection updates a nested
property inspector and a line-numbered full-source preview. Scalar leaves such
as `application.name`, `label`, `fields.number.length`, Boolean settings and
sequence values can be edited with their existing YAML type. Mapping/sequence
containers, `schema_version`, and semantic `entity`/`view`/`report` identities
are visibly locked; those identities require a future cross-document rename
operation rather than a single unsafe property replacement.

Enter or **Apply in memory** sends a typed `set_value` command to the bounded
`DesignerService` session and recompiles the complete candidate. A valid or
invalid result remains inspectable. **Changes** shows the exact unified diff,
**Diagnostics** shows compiler messages, and Undo/Redo use the shared Designer
history. Shortcuts are `Ctrl+Z`, `Ctrl+Y`, `Ctrl+D`, and `Q`. `R` reloads clean
sessions but refuses to silently discard pending edits. Outside expert YAML
editing, `Ctrl+S` opens the save review.

**Save candidate** is enabled only for a valid modified candidate. Its modal
shows the canonical project, changed YAML files, receipt destination, exact
diff and the complete candidate-bound `SAVE tide-designer-approval-...`
challenge. The confirmation button remains disabled until that exact phrase is
entered. Approval invokes only `DesignerSaveService`, which rereads the live
base, reacquires the evidence, locks, stages, recompiles and transactionally
replaces the approved YAML before publishing its receipt. Studio then reopens a
fresh clean Designer session with no undo history crossing the saved baseline.

A stale live base or active/interrupted save lock blocks approval. When a lock
is present, the review also performs the existing read-only recovery inspection
and displays the precise `tide designer recover ... --preview` command; recovery
itself remains a separate explicit evidence-bound operation. Normal Studio
editing executes no application Python and never opens the configured database.
The property and YAML widgets still have no direct file-write authority, and
closing Studio discards any candidate that has not passed the save review.

### Editor ergonomics

Studio does not present every scalar as an unstructured text box. The property
descriptor now derives editor metadata from the same Pydantic/source JSON
Schema used by the compiler. `Literal` and enumerated values become dropdowns—
for example `fields.id.type`, view `kind`, `on_delete`, write ownership and
editor kinds—and Boolean values use a true/false selector. These choices are
not copied into the Textual adapter, so future Qt, Web and AI clients can reuse
the same contract. Richer numeric controls, path/reference selectors, help text
and conditional-property hints remain planned.

The lower panel now enables terminal-theme-aware YAML syntax coloring when the
`studio` optional dependency is installed. It uses Textual's syntax support and
tree-sitter YAML parser while retaining a plain-text fallback for minimal `tui`
installations. `Ctrl+F` opens case-insensitive search over the current YAML,
unified diff or diagnostics. Enter/Next and Previous wrap through matches,
show the current/total count and select the active occurrence. Dedicated diff
token coloring remains planned.

The explicit **Edit YAML** mode makes the current source buffer editable for
experienced developers. **Apply YAML** or `Ctrl+S` sends the buffer through the
bounded `replace_document_source` command; `Esc` cancels and restores the
session source. Applying requires strict YAML, preserves the selected
document's semantic identity, updates only the process-local candidate, runs
the compiler, opens the exact diff, and joins the same undo/redo history.
Malformed YAML leaves the editor open so it can be corrected or cancelled.
Search remains available while editing. Persistence requires **Save candidate**
and its separate candidate-bound approval/save workflow: **Apply YAML** itself
does not write a file and the text widget has no direct file-write authority.

## TUI view designer

A Textual designer can dogfood TIDE and support:

- model and component trees;
- keyboard and mouse selection;
- move, nest, unnest, resize, and reorder operations;
- fields, groups, tabs, collections, and action bars;
- property inspector;
- undo, redo, clipboard, and source diff;
- preview at several terminal dimensions;
- preview as a selected role;
- resolved-model explanation.

The first version should favor structural tree editing over free-form dragging.
Move-up, move-down, nest, unnest, and property editing provide most practical
value with substantially less complexity.

The first structural slice is now present whenever a view document is selected
in Textual Studio. Its dedicated table resolves the compiled view and entity,
then displays these terminal placement tracks:

- browse, lookup, and inline collection table columns in left-to-right order;
- form fields in the renderer's left and right columns;
- inline-editor `layout.rows` as complete left and right column-first tracks.

Each row shows the field, label, type, source group and layered metadata origin.
The adjacent summary presents the resolved order without requiring the
developer to infer it from YAML. **Move up** and **Move down** operate only
inside the selected track. Flat `columns` use the bounded sequence-move command;
layout fields atomically swap their exact YAML slots in one command batch. Both
paths preserve the layout container, recompile immediately, join normal
undo/redo, update the exact diff, and persist only through **Save candidate**.
Inherited or generated tracks with no local source path remain preview-only.

The next slice adds conservative cross-column and membership operations. A
local form or inline field can **Swap left** or **Swap right** only when a local
field occupies the same resolved position in the opposite track and both are
inside the same YAML group. The two exact scalar slots swap atomically, so the
operation never invents blank placeholders or silently crosses metadata groups.
Ordinary up/down movement is now group-bounded for the same reason.

The entity-field selector lists fields not currently present in the locally
owned structure. **Add field** appends a browse/lookup column, adds a form field
to the selected field's group, or updates both inline `columns` and
`layout.rows` in one transaction. Inline choices exclude collection, readonly,
computed, and hidden fields. **Remove field** deletes only the view placement,
never the entity field or database column; inline removal again updates table
and editor membership together. Add/remove, swaps and moves all recompile,
participate in undo/redo and exact diff, and persist only through **Save
candidate**.

Form and inline additions now use an explicit destination-group selector. The
**Groups…** dialog exposes compiler-resolved group order and field counts, and
can create, rename, move, or remove a local group. Group movement is limited to
an immediately adjacent group: collection sections are deliberate barriers
until collection structure joins the designer. Removal is available only for
an empty group, so it cannot silently delete field placements. Group operations
use the same bounded commands, history, diff, compile, and approved-save path.

The next structural slice is also executable. The **Layout…** dialog treats
groups and collections as one ordered section track, assigns shared portable
tab labels, adds unused collection fields only when a compatible inline editor
exists, and removes collection placements without deleting entity fields. It
also edits the ordered record action bar (`cancel`, `save`, and domain actions)
and each collection action bar (`add`, `apply`, `remove`). The Textual runtime
renders declared tabs and button order, while absent metadata keeps generated
defaults. The compiler validates the contract and every Studio operation uses
the same in-memory history, exact diff, and approval-required save boundary.

The adjacent **Preview…** dialog now resolves the selected candidate view for
any compiled application role and for compact (80×24), standard (100×30), or
wide (140×40) terminals. It shows entity-operation access, protected/read-only/
editable/record-dependent field placements, record and collection action
states, tab/section order, declared minimums, estimated height, horizontal-
scroll pressure, and a canvas with the exact selected width and height. The
preview constructs a synthetic `Channel.TUI` principal and calls the shared
`SecurityEngine`; it never loads a record, opens the configured database, or
executes application code. Consequently, row-dependent immutability and action
conditions are labeled conditional rather than guessed from invented data.

The closing hardening pass keeps preview and runtime semantics aligned:
`fields.<name>.hidden: true` is now honored by live browse columns, form fields,
and collection sections as well as by the preview. On compact developer
terminals the Studio details pane scrolls instead of clipping its lower tools,
and the YAML editor retains a usable minimum height. If an in-memory candidate
cannot compile, the selected view keeps the compiler explanation visible while
all structural and preview actions fail closed until undo or another edit
restores a valid candidate.

Transferring an unmatched last field between columns is deferred until the
portable layout model defines an explicit empty-cell/span concept. The resolved
contract remains UI-independent so a later Qt or Web designer can reuse it.

## Web view designer

A local browser-based TIDE Studio may later add:

- responsive breakpoint previews;
- drag-and-drop structural layout;
- theme and design-token editing;
- accurate typography and page sizing;
- web-renderer-specific properties;
- side-by-side TUI and web previews.

Using a browser for development tooling does not make deployed TIDE
applications web applications.

## AI-assisted design

The developer MCP now exposes read-only project/model/view resources and a
no-write structured new-application proposal tool. An agent can already express
entities, relationships, roles, safe state transitions and record/PDF report
intent without receiving arbitrary filesystem or Python execution authority.
The proposal returns an approval-required deterministic ID and semantic
diagnostics. The companion preview tool materializes a deleted candidate,
generates conventional browse/form/lookup/inline views, compiles it, and runs
bounded in-memory CRUD/security/action/report/HTML/optional-PDF checks using
only fixed TIDE templates. It returns exact artifacts, hashes and a diff but
cannot apply them.

DesignerService now extends the structured command/change-set boundary to an
in-memory copy of an existing application. An agent can inspect an entity or
view, propose typed property/order changes, compile them, undo/redo them, and
present the exact comment-preserving YAML diff. The local DesignerSaveService
can persist the same structured batch only after a freshly checked base and
candidate-bound human approval. Developer MCP intentionally does not expose
that save capability yet.

Useful tools include:

```text
tide_get_resolved_view
tide_create_view
tide_move_view_node
tide_set_view_property
tide_validate_view
tide_preview_view
tide_preview_report
```

## Report model

Reports use a declarative banded model inspired by classic business report
formatters:

```text
Report Header
Page Header
Group Header
Detail
Group Footer
Page Footer
Report Footer
```

An initial invoice report may look like:

```yaml
report: sales.invoice
title: Invoice
entity: sales.Invoice
permission: sales.invoice.report
expose: {rest: true}

parameters:
  invoice_id: {type: integer, required: true}

query:
  criteria: "id == $invoice_id"

bands:
  report_header:
    - text: Invoice
      style: report_title

  record_header:
    - field: number
      label: Invoice number
    - field: customer
      label: Customer

  detail:
    source: lines
    columns: [product, quantity, unit_price, total]

  report_footer:
    - field: total
      label: Total
      format: money

  page_footer:
    - expression: "'Page ' + page_number"
```

The executable v0.1 subset is intentionally a secured `kind: record` report.
Its query must bind the entity primary key to one required parameter, so the
runtime can perform an indexed, row-policy-aware `RecordsService.get` instead
of loading and filtering a table in memory. The compiler validates report
access, parameter and expression types, root fields, the detail collection,
detail columns, and named formats. Reports fail closed if any requested field
is protected. REST delivery is independently opt-in through
`expose.rest: true`; declaring a report permission alone does not create an
HTTP route.

## Report capabilities

The intended progression includes:

- typed required parameters with validation; **implemented for record reports**
- sorting, filtering, grouping, and totals;
- shared formats and computed expressions; **implemented**
- page size, orientation, and margins;
- repeating table headers and numbered page footers; **implemented in PDF**
- page breaks and keep-together behavior;
- tables, text, images, and later barcodes;
- standalone HTML output, native A4 PDF output, and TUI preview; **implemented**
- controlled CSV and spreadsheet export;
- subreports after the core band model is stable.

A browser-based designer is the likely primary visual report designer because
it can show a realistic page canvas. A TUI editor can still provide band-tree
and property editing.

## Reporting security

Reports request data through secured record and report services. They may not
create unrestricted SQLAlchemy sessions. Entity, row, field, report, and export
permissions all apply. Protected fields and computed-field inference rules
remain active. Durable report/export audit is still a future operational
contract and is not implied by the initial renderer.

## Initial rendering strategy

`ReportService` first creates an immutable, renderer-neutral `ReportDocument`
containing only authorized, already formatted values. Textual renders that
document as a terminal preview. A standard-library renderer writes standalone
print CSS/HTML, while the optional `report` package extra uses ReportLab to
write A4 PDF directly with Unicode-capable system-font discovery, repeating
table headings, numeric alignment, and page numbering. Output defaults to
`output/reports/` below the process working directory.

For remote Textual mode, the server builds this same secured document and
transports its versioned structure. The client validates it and reuses the
ordinary preview/HTML/PDF renderers; raw records and database credentials never
move to the reporting client.

This establishes the adapter boundary without claiming a pixel-perfect report
engine. Grouping, images, arbitrary result-set reports, configurable page
geometry, keep-together controls, and durable report audit remain later work.
