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

## Semantic layouts

Shared layouts describe structure rather than pixel or terminal coordinates:

```yaml
view: sales.Invoice.edit
entity: sales.Invoice
kind: form
extends: standard_form

layout:
  - group: Invoice
    rows:
      - [number, invoice_date, status]
      - [customer]

  - collection: lines
    view: sales.InvoiceLine.inline_edit

  - group: Totals
    align: right
    rows:
      - [subtotal]
      - [tax]
      - [total]
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

The target is shared application semantics with limited renderer-specific
presentation, not an identical lowest-common-denominator interface.

The first executable renderer consumes resolved browse, form, inline-edit, and
lookup views directly. It builds `DataTable` columns from view metadata, queries
only through `RecordsService`, resolves reference display text through secured
record reads, and carries opaque continuation cursors for browse navigation.
Reference fields use compact selectors by default; `editor: lookup` opens a
secured, case-insensitive, multi-column search window. Keyboard bindings and
buttons invoke the same service operations.

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
