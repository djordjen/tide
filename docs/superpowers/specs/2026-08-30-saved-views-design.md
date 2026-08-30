# Saved views — design

Date: 2026-08-30. Decided with Djordje: a saved view **snapshots** its column
arrangement (null means "follow the standing arrangement"); per-user only;
search text stays out.

## What this is

A person names the state of a browse grid — the named filter, the funnel
checks, the sort, and the columns as arranged — and gets it back from the
named-filter dropdown: "Overdue invoices", "This quarter by customer". The
XAF ListView-variant idea executed over the personalization contract the
column chooser proved.

## What a saved view stores

The **components** of the screen, not a flattened filter list — because
restoring must relight the controls (the funnel stays lit, the dropdown names
the filter), and a grid whose rows are constrained by conditions its controls
do not show is lying:

```json
{
  "named_filter": "drafts" | null,
  "value_filters": {"status": ["draft", null]},
  "sort": [{"field": "total", "descending": true}],
  "columns": [{"name": "number", "label": "No."}] | null
}
```

- `named_filter` must be a filter the view declares, or null.
- `value_filters` maps filterable fields to checked values, stored as the
  wire's own JSON; a stale value simply matches nothing at use time, because
  the query service re-validates every replayed condition — no second
  validation of values here.
- `sort` fields must be sortable; `columns` follow the arrangement rules
  (real, readable, non-collection, no repeats, bounded labels), or null to
  follow the standing per-view arrangement.
- Search text is deliberately not stored: typing in the search box is a
  transient gesture, not a view definition.

## Storage and service

`tide_saved_view` — the seventh `FrameworkStores` field. One row per
(principal, view, name); name is the user's label, 1–60 characters after
trimming. At most 20 saved views per (principal, view), refused beyond —
a bound the server owns. Legacy databases and `--demo` degrade to a
process-local store, as sessions and arrangements do.

`SavedViewService(model, security, rows)` owns validation once for every
transport: real browse view, the rules above, every reason named at once.

## REST

- `GET    /api/v1/_tide/saved-views/{view}` → `{"views": [...]}`, each entry
  the document plus its `name`.
- `PUT    /api/v1/_tide/saved-views/{view}/{name}` upserts one → 204;
  refusals 400 in the house error shape; unknown/non-browse view 404.
- `DELETE /api/v1/_tide/saved-views/{view}/{name}` → 204.

No manifest change: saved views are pure user state, fetched per view.
Cookie sessions send `X-TIDE-CSRF` on the unsafe verbs as everywhere.

## Web

The named-filter dropdown renders when the view declares filters **or** the
person has saved views. Below the declared filters: a "Saved views" section
listing the person's entries (delete beside each), and "Save current view…"
opening a small name dialog. Radio values are namespaced (`saved:` prefix) so
a saved view can share a label with a declared filter without colliding.

Selecting a saved view applies its components wholesale: named filter, funnel
checks, sort, and — when it carries columns — a columns override that behaves
exactly like an arrangement (order authority included). Selecting "All
records" or a declared filter afterwards keeps today's semantics (funnels
compose, nothing else resets) but clears the columns override and the active
saved-view label. Edits after selection are just edits, XAF-style: the label
stays until you choose something else, and "Save current view…" under the
same name is the explicit way to update it.

Capture and apply are pure functions in `lib/saved-views.ts`, unit-tested;
the journey proves the round trip against the real stack.

## Deliberately out

- Role-shared saved views (declared-filter territory).
- Auto-saving edits back into the selected view.
- Search text.
- TUI and MCP abstain; export keeps sending what the grid's controls hold.

## Testing

Store round-trip, isolation, upsert, the 20-entry cap; service refusals with
every reason named; REST auth + verbs + 404s; pure capture/apply unit tests;
a Playwright journey — funnel + sort + arrange, save, switch away, reselect
(controls relight), reload (server kept it), delete.
