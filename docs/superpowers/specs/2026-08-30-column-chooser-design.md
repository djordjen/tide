# Browse column chooser — design

Date: 2026-08-30. Decided with Djordje: server-stored per user; parent-type
property paths deferred as their own contract decision; per-user renaming only
(no role-shared push).

## What this is

A person reading a browse grid can choose which columns it shows, order them,
and rename their labels — per user, per view, stored on the server so the
choice follows them across browsers. The XAF ListView column chooser, shaped
for TIDE's contracts.

The YAML stays the only declaration: `columns:` on the view is the default
every principal starts from. The user's choice is an overlay stored beside the
data, the way XAF layers user differences over the application model — it is
state, not a second declaration, so the one-rule-one-place ruling holds.

## Why it is cheap

Three facts already in the code carry most of the feature:

1. `_project` returns every readable field of the entity, protected ones as
   the sentinel — a list row already holds the values for columns the view
   never declared.
2. `_require_queryable_field` validates filter/sort/summary fields against the
   entity and the principal, not against the view's columns — the security
   boundary under the chooser exists.
3. `_column_contract` builds a manifest column from any field name.

## Contract changes

### Manifest

`TideBrowsePresentation` gains `available_columns`: every field of the entity
the principal can read whose type is not `collection`, in entity declaration
order, built by `_column_contract`. Optional-with-default on the wire for the
same version-skew reason as `summaries`.

`sortable_fields` and `filterable_fields` widen from the displayed columns to
the available set, through the same field-type rules (`browse_sortable_fields`
/ `browse_filterable_fields`). The lists were presentation capability, never a
security boundary; the service refuses what it always refused.

### Storage

A sixth `FrameworkStores` field: `view_state`, table `tide_view_state`,
composite key `(principal, view)`, one JSON document per row:

```json
{"columns": [{"name": "number", "label": "No."}, {"name": "total"}]}
```

Array order is column order; a missing `label` means the declared one. The
framework-schema derivation covers create/validate/diff/backup without being
told. Legacy databases (no framework tables) and `--demo` fall back to a
process-local in-memory store, the same degradation browser sessions take.

### Service

`ViewStateService(model, security, rows)` owns validation:

- the view must exist and be a browse view;
- every column name must be a field of the view's entity, not a `collection`,
  and readable by the principal;
- names must not repeat; at least one column; a label is trimmed, 1–80 chars.

`get(context, view)` → stored columns or `()`. `put(context, view, columns)`
validates and stores. `delete(context, view)` resets.

### REST

Three routes beside `/_tide/session`, request-context authenticated:

- `GET  {base}/_tide/view-state/{view}` → `{"columns": [...]}`; an empty
  array means no customization.
- `PUT  {base}/_tide/view-state/{view}` with the document → 204; validation
  failures 400, unknown/non-browse view 404.
- `DELETE {base}/_tide/view-state/{view}` → 204.

### Web

A `Choose columns` icon button in the browse toolbar beside Export — not in a
header cell, which is already carrying sort, funnel and the resize handle
(D13). It opens a popover: one row per available column with a checkbox and,
when checked, a label input whose placeholder is the declared label; Move
up/down buttons order the checked set; Apply saves (PUT), Reset to default
deletes (DELETE), Close discards. Transient adjust-in-place, per the modality
rule.

The workspace fetches the state with react-query keyed by application,
principal and view, computes the effective column list (state over
`available_columns`, label overrides applied), and passes `{...view, columns:
effective}` down — the grid, funnels, sorting and footer summaries all read
`view.columns` and follow without change.

## Deliberately out

- **Parent-type property paths** (`customer.city`): a query-contract change —
  joins, cross-entity security, legacy scope. Deferred as its own decision,
  same family as TIDE306 and B8.
- **Role-shared column sets**: per-user only; pushing a set to a role is
  declared-view territory.
- **Export**: `_export` keeps the view's declared columns — the server owns
  that contract and the export is the view, not one person's arrangement.
- **TUI and MCP abstain**, the same split as global search and export.
- **Column widths** stay client-side; this stores set, order and labels.

## Testing

Store round-trip and principal/view isolation; service validation (unknown
field, collection, unreadable, repeats, empty, label bounds); REST auth and
the three verbs; manifest `available_columns` content and widened lists;
vitest for the chooser (toggle, rename, reorder, apply/reset) and for the
workspace's effective columns; one Playwright journey — add a column the view
does not declare, rename one, reload to prove the server kept it, reset to
default.
