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
HTTP-backed services and can be rendered later by Qt/Web.

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
