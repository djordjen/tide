# Presentation Model

## Generated defaults and overlays

Every entity produces useful default browse, edit, and lookup views without a
designer. Applications customize these views through deterministic overlays
instead of copying generated output.

The resolution order is:

```text
Framework defaults
        -> application defaults
        -> named preset
        -> entity presentation settings
        -> specific view overlay
        -> permitted deployment/user preferences
        -> runtime security enforcement
```

Security is never weakened by an overlay or preference.

## Shared defaults

Application-wide behavior belongs in `presentation/defaults.yaml`:

```yaml
navigation:
  - label: Sales
    items:
      - view: sales.Invoice.browse
  - label: Master Data
    items:
      - view: crm.Customer.browse
      - view: catalog.Product.browse

browse:
  page_size: 25
  incremental_search: true
  zebra_stripes: true
  confirm_delete: true
  keymap: standard
  actions: [new, edit, delete, refresh, close]

form:
  label_position: left
  label_width: 18
  show_required_indicator: true
  validate_on_leave: true
  keymap: standard
  actions: [save, cancel]

lookup:
  page_size: 15
  incremental_search: true
  close_after_selection: true
```

`navigation` is the portable application workspace definition. Groups and
items retain YAML order, every item must name one browse view, and a browse
view may appear only once. An omitted item label uses the target entity label.
The compiler rejects empty groups, invalid labels, unknown/non-browse views,
and duplicates with `TIDE249`.

Renderers intersect this definition with the authenticated principal's list
capabilities and remove empty groups. Qt renders the result as a grouped
application sidebar and lazily keeps each visited workspace alive, preserving
its query, loaded records, selection, and per-view column settings. Textual's
compact workspace selector uses the same item order and labels. `--view`
remains a deep-link/startup override; an accessible view omitted from explicit
navigation is made reachable for that launch without changing YAML. Future Web
navigation must consume this same normalized contract rather than interpreting
the source independently.

Remote renderers receive the safe browse subset at
`GET /api/v1/_tide/presentation`. The versioned manifest contains only
navigation items the principal can list and only columns that principal may
read. Search, named filters, and sort choices are removed when they depend on a
protected field. It also supplies the generated resource/query paths, identity
field, labels, types, formats, alignment, reference targets, fetch size, and
available operations. This is presentation discovery, not authorization:
FastAPI rechecks every subsequent request. Raw YAML, expressions, credentials,
and hidden or inaccessible metadata are not transferred to the renderer.

For browse views, `page_size` is the bounded server fetch batch, not a
requirement to expose page-navigation controls. Textual and Qt fill the visible
list, retain already loaded rows, and request the next opaque cursor batch near
the scroll boundary. Sorting, filtering, searching, switching views, or
refreshing starts a new secured cursor sequence.

Qt existing-record forms may expose **Previous** and **Next** without
reintroducing page navigation. Qt maps Page Up and Page Down to those actions
at dialog scope. Adjacency is resolved from the current browse query's loaded
identities; moving forward from its last loaded row requests the next opaque
cursor batch and then opens the adjacent identity through the authenticated
record API. The renderer replaces values, editability, actions,
collections, ETag, title, and navigation state within the same dialog so its
position and size stay stable. It keeps the source form intact on failure and
rejects navigation while its supported draft differs from the original. These
controls are navigation affordances, not authorization or storage paths.

The compiled browse column order remains the portable default shared by every
renderer. Qt and Web may layer a local personal order and widths over that
default, keyed by application, view, and authenticated principal. Dragging a
heading or resizing a divider updates only that renderer's local settings; it
never edits YAML, changes another user's layout, or alters the TUI/default.
Stored layouts use stable field names, so known columns survive metadata
additions or removals and new columns fall back into metadata order. **Best
Fit** sizes columns from currently loaded contents only. **Reset Layout**
removes the personal override and restores metadata order plus default fitted
widths. A new Web layout applies Best Fit once after its first loaded batch; it
does not continuously resize after refresh or fetch an entire remote dataset
for measurement.

Publishing a changed shared default is a distinct privileged designer action.
It must use the compiler-validated Designer service and approved YAML save
boundary rather than promoting a personal GUI setting implicitly.

The first editable Qt form tranche was deliberately limited to flat forms. It
uses compiled group/row placement, traverses the left field column before the
right with Tab or Enter, distinguishes read-only fields, and chooses Boolean,
choice, numeric-mask, regex-mask, and ordinary text editors from field
metadata. Session capabilities are only advisory control visibility: create
and update requests still go through FastAPI, where permissions, row rules,
normalization, uniqueness, validation, and concurrency remain authoritative.
Blocking form loads and saves run outside the GUI thread, and updates send the
observed strong ETag when the entity defines one. The later contracts below add
references, transactional collections, conflict review, and form-domain
actions.

The next Qt tranche extends that form contract to compiler-approved reference
lookups. A reference must opt into `editor: lookup` and resolve a lookup view
whose target is list-accessible to the authenticated principal. Qt renders its
readable columns, debounces search across the declared search fields, and
performs each typed query outside the GUI thread. Choosing a row does not
assign it locally: Qt calls the shared reference-selection endpoint so
`on_select` defaults, field-write authorization, and protected source values
remain server-owned.

When the reference configuration declares `allow_create: true`, resolves a
target form, and the session advertises target create access, **New** opens that
same metadata-driven Qt form. **Save & Select** commits the referenced record
independently and then applies its identity to the still-unsaved parent draft.
Invoice editing uses this contract for Customer and for Product inside
InvoiceLine.

The Qt inline-collection contract resolves the collection field, target entity,
inline view, table columns, editor rows, actions, nested draft operations, and
writable fields. The InvoiceLine table takes flexible height above the Line
Details fields; the editor follows metadata row placement with column-first
focus order, and Add/Apply/Remove remain explicit local-draft operations.
Product selection goes through the shared server reference-selection endpoint,
so Description and Unit Price assignments use the same authorization and
`on_select` semantics as Textual and API clients.

Applying a line evaluates stored computed fields locally for a non-authoritative
line/Invoice total preview. Final Save applies the selected line, includes
existing nested identities, and filters inverse, computed, read-only, protected,
and unknown fields before one ETag-protected parent mutation. The application
service remains authoritative for normalization, validation, recomputation,
row/field policy, concurrency, and the transaction. If nested draft capability
is absent, Qt keeps the collection visible but read-only rather than bypassing
the server contract.

Textual consumes the resolved browse action list rather than inventing a
separate toolbar contract. `delete` is shown only when it is present in that
list and the current principal has the entity's explicit delete permission.
With `confirm_delete: true`, the selected record's display value is shown in a
modal whose safe default is **Keep record**; Escape also cancels. Confirmation
calls `RecordsService.delete()` with the observed version, then refreshes the
browse. Reference restrictions remain service errors and are translated into a
relationship-aware message without giving the renderer repository access.

Record-edit concurrency follows the same renderer/service split. When a commit
reports `stale_version`, the TUI reads the current secured record and passes the
original, current, and draft values to the shared three-way conflict comparer.
The review surface labels genuine overlaps separately from changes made only by
the current user or another user. Users may keep the draft open, discard it and
reload, or explicitly select **Use Current**/**Use Mine** for every overlapping
field. A complete resolution plan carries draft-only and explicitly selected
values into a fresh `RecordSession`; it never mutates storage from the dialog.
Field permissions and `immutable_when` are reevaluated against the current
record before rebasing, so a concurrent workflow transition cannot carry an
edit into a newly read-only field. The user reviews and saves the resulting
form through normal validation. The same contract works with local and
HTTP-backed services and is now rendered by both Textual and Qt.

Qt reacts to the typed API `stale_version` error by re-reading the current
secured record and passing the same field set to that comparer. Its modal table
shows Original, Current, Your draft, and Resolution columns. A complete plan
reopens a new metadata-driven form with the current ETag; the dialog itself
never retries or writes. Current-only values win automatically, draft-only
values are carried when still writable, and every overlap requires an explicit
Current/Mine choice. Nested lines are intentionally one collection conflict
unit until TIDE defines stable row identity and ordering semantics for a safe
child-level merge.

Qt form-domain actions are the intersection of the compiled form action order,
entity action definitions, and the authenticated session capability list.
`visible_when` and `enabled_when` are reevaluated against the current local
draft for responsive controls, but remain advisory: FastAPI authorizes and
validates execution again. The first rendered action is Invoice **Post**.

When an action is invoked, Qt applies the selected collection row and validates
the draft. A changed create/update draft is committed first; the action request
then carries the ETag returned by that commit and a unique `qt:` idempotency
key. If the save succeeds but the action fails, the saved form is reopened with
its current ETag and the failure message. A stale failure enters the same
three-way review contract rather than introducing last-write-wins behavior.

Form `layout` is a shared semantic contract, not a renderer hint. One
renderer-neutral resolver produces ordered group rows, collections, tabs,
actions, and hidden-field decisions for Textual and Qt; the Web detail renderer
must consume the same result. A row such as `[number, invoice_date]`
therefore means the same visual pairing everywhere. Surface-specific metadata
may control measurements, but it may not independently regroup fields.
`settings.compact_groups: true` is also portable: Studio retains the authored
groups, while renderers resolve their scalar fields in order into one
two-column header before collection sections.

Qt record reports follow the same renderer-neutral boundary as Textual. The
active entity's record report is visible only when it is REST-exposed and
present in the authenticated session capabilities. FastAPI builds and
authorizes the `ReportDocument`; Qt renders it to a unique GUI-session file
under the operating system's temporary directory on a worker thread, then asks
the system PDF viewer to open it. Report metadata and permissions remain
authoritative across renderers.

Qt parameterless summary reports use the same boundary. A summary action is
available only when the report belongs to the active entity, is REST-exposed,
has no declared parameters, and appears in the authenticated session
capabilities. The server builds the full authorized `ReportDocument` outside
the GUI thread; Qt displays its detail table and offers the shared controlled
CSV, HTML, and PDF writers. Current browse search/filter/cursor state does not
silently alter the report's declared query. Parameter-entry controls remain a
separate later contract.

Named presets capture recurring patterns such as `standard_browse`,
`standard_form`, and `master_detail`.

## View overlays

A view mentions only meaningful differences from its generated base:

```yaml
view: crm.Person.edit
base: generated.edit
mode: overlay
extends: standard_form

settings:
  title: Person Details

fields:
  internal_code: {hidden: true}
  email: {width: 40}
  notes: {height: 5, span: full}
```

Unmentioned fields continue to follow inherited and generated behavior. This
allows a newly added model field to appear automatically unless a view has
chosen an explicit fixed layout.

`hidden: true` removes the field from the resolved view's live TUI placement,
including browse columns, form controls, or a collection section and its action
bar. It remains presentation metadata, not an authorization rule; services
continue to enforce field and entity security independently.

## Semantic layouts

Shared layouts describe structure rather than pixel or terminal coordinates:

```yaml
view: sales.Invoice.edit
entity: sales.Invoice
kind: form
extends: standard_form

layout:
  - group: Invoice
    tab: Details
    rows:
      - [number, invoice_date, status]
      - [customer]

  - collection: lines
    tab: Details
    view: sales.InvoiceLine.inline_edit
    actions: [add, apply, remove]

  - group: Totals
    tab: Summary
    align: right
    rows:
      - [subtotal]
      - [tax]
      - [total]

actions: [cancel, save, post]
```

The Textual renderer converts this structure into character-cell layouts. A
future web renderer uses responsive layout rules. Surface-specific adjustments
remain possible:

```yaml
surfaces:
  tui:
    minimum_width: 100
    collection_height: 14

  web:
    maximum_content_width: 1400
    collapse_header_below: 800
```

Inline collection editors honor the order declared by `layout.rows`
independently of the collection table's `columns` order. Each row may contain
one or two fields; the Textual renderer places the first field in the left
column and the second in the right, then traverses the complete left column
before the right column. When no inline layout is declared, the renderer falls
back to the editable `columns` order. This lets an invoice line editor place
`product` before `description` without changing the line table layout.

Studio derives the same resolved terminal tracks from the compiled view. For
view-local `columns` and `layout.rows`, developers can move a field up or down
inside its current table, form, or inline track. The operation is expressed as
a bounded sequence move or atomic slot-swap command batch, so compiler
validation, provenance, exact diff, undo/redo, and approved persistence remain
consistent with raw YAML authoring. Inherited or generated tracks are displayed
but remain read-only until an explicit overlay-creation operation is added.

Local layout fields may also swap left/right with the same-position field in
the opposite track when both placements belong to the same group. This strict
swap rule avoids unsupported empty cells and prevents an apparently visual
operation from changing group ownership. Studio can add an unused entity field
to locally owned columns/layout and remove a view placement without touching
the entity definition. Inline add/remove changes `columns` and `layout.rows`
atomically so the editor's completeness rule remains valid.

Form/inline additions choose a destination from the resolved local field
groups. Studio can create and rename a group, reorder it across an adjacent
field group, and remove it after it becomes empty.

Form layout sections may declare a portable `tab` label. Sections with the same
label share one tab; unlabelled sections appear under **General** when any tab
is declared. A collection section may order any subset of `add`, `apply`, and
`remove`, while a form-level `actions` sequence orders `cancel`, `save`, and
the entity's declared domain actions. Omitting either action sequence preserves
the generated defaults. The compiler rejects empty/unsafe tab labels, duplicate
or unknown actions, and collection views that are not compatible inline editors
(`TIDE244`).

Studio's **Layout…** dialog edits that same shared contract. It assigns/clears
tabs, moves complete group or collection sections, adds an unused collection
only with a compatible inline view, removes only the view placement, and edits
record/collection action sequences. These operations are still bounded,
compiler-validated, undoable, diffed, and persisted only through the approved
Designer save boundary.

The target is shared application semantics with limited renderer-specific
presentation, not an identical lowest-common-denominator interface.

The first executable renderer consumes resolved browse, form, inline-edit, and
lookup views directly. It builds `DataTable` columns from view metadata, queries
only through `RecordsService`, resolves reference display text through secured
record reads, and carries opaque continuation cursors for browse navigation.
Reference fields use compact selectors by default; `editor: lookup` opens a
secured, case-insensitive, multi-column search window. Keyboard bindings and
buttons invoke the same service operations.

A lookup may allow nested record creation without closing the parent draft:

```yaml
fields:
  product:
    editor: lookup
    allow_create: true
    create_view: catalog.Product.edit
```

The compiler requires the create view to be a form for the referenced entity.
At runtime, **New** is available only when the current principal has entity
create access. **Save & Select** commits the independent lookup record, closes
the nested form and lookup, selects the new reference, and applies the normal
`on_select` assignments. Cancelling the parent draft does not remove the newly
created master record.

## Semantic formats

Formats centralize Clarion picture-like behavior:

```yaml
formats:
  money:
    decimal_places: 2
    thousands_separator: true
    align: right
    tui_width: 14

  percentage:
    decimal_places: 1
    suffix: "%"
    align: right

  local_date:
    display: "%d.%m.%Y"
    input: ["%d.%m.%Y", "%d/%m/%Y"]
```

A format can influence TUI forms, browses, reports, parsing, and exports. REST
normally returns a machine-readable raw value rather than the formatted display
string.

## Edit masks

Field-level edit masks constrain input independently of display formatting:

```yaml
fields:
  unit_price:
    type: decimal
    precision: 12
    scale: 2
    edit_mask: "0.00"

  code:
    type: string
    length: 30
    edit_mask: {regex: "[A-Z][A-Z0-9-]{0,29}"}
```

The numeric picture `0.00` allows one decimal separator and at most two
fractional digits; on leaving the Textual editor, an entered trailing separator
is padded to the fixed number of places. `0` is the corresponding integer
picture. A comma may be used in the picture for applications that prefer a
comma decimal separator.

Regular expressions validate the completed value rather than trying to infer
which partial keystrokes might eventually become valid. The compiler checks
the expression, `RecordsService` enforces it for every adapter, and OpenAPI
publishes it as the string schema pattern. A renderer may additionally show
validation while the user edits. Numeric precision and scale are likewise
enforced by services; a mask improves entry but never replaces validation.

## Actions and keymaps

Views present first-class actions rather than implementing commands locally.
Shared keymaps assign conservative terminal shortcuts, while a view may add or
remove presentations without changing the action handler.

Keyboard and mouse operations must reach the same action. A button click and
`Ctrl+P` are two presentations of `sales.Invoice.post`, not separate code paths.

## Conditional presentation

The expression system may control non-security presentation behavior:

```yaml
fields:
  cancellation_reason:
    visible_when: "status == 'cancelled'"

  invoice_date:
    editable_when: "status == 'draft'"
```

Application services still enforce write rules and action preconditions. A
hidden or disabled widget is not authorization.

## Diagnostics

Resolved views must be explainable:

```bash
tide view explain sales.Invoice.edit
```

The result should show the final property value and the layer that supplied it.
This is essential once defaults, presets, entity settings, overlays, and user
preferences coexist.
