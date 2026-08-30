# Column operator filters

Date: 2026-08-30. Status: approved ("proceed with the filters" over the
pitch defaults: range for date/numeric, contains for text, one active
mode per column, TUI abstains).

## What

XAF's auto-filter row, funnel-shaped: a column's filter popover can now
express more than membership. By field type:

- **date** — a From/To pair of native date inputs (either side may be
  open-ended), replacing the distinct-value checklist outright: a list
  of individual dates was never the question.
- **integer / decimal** — a Min/Max pair, same shape.
- **string** — a two-mode toggle, *Values* (the existing checklist) or
  *Contains* (one text input, matching the search box's
  case-insensitive semantics).
- **choice / reference / boolean / uuid** — the checklist, unchanged.
- **datetime** — the checklist, deliberately unchanged for now: a range
  over aware datetimes walks into the deferred B7 normalization item,
  and the daily wins (invoice dates, totals) are date and decimal.

**One active mode per column** (the ratified rule): a range replaces a
checklist choice and vice versa; the string toggle switches which mode
is being staged, and Apply commits exactly one kind.

## Nothing new on the wire's query side

`TideFilterInput` already carries the operators (`gte`/`lte`/`in`/
`icontains`), and the query service already validates and executes them
— named filters and MCP search use them today. The funnel simply stops
being the only caller that never emits them. Wire values follow the
house types: integers as numbers, decimals as exact strings, dates as
ISO text.

## One column-filter state, one composition

The workspace's per-column state widens from `Record<string, unknown[]>`
to one discriminated `ColumnFilterState` per column — `values`, `range`,
or `contains` — owned in one place and read by the grid, the funnel, the
shared `gridStateFilters` composition, and saved-view capture alike
(learning 38's authority rule, applied before the second owner can
exist). `gridStateFilters` maps kinds to conditions: values → one `in`,
range → up to two of `gte`/`lte`, contains → `icontains`. A column's
funnel keeps seeing every condition except its own, whatever kind its
own is.

## Saved views carry the new kinds

The saved-view document gains `conditions` — a list of
`{field, operator, value}` — beside the untouched `value_filters`
membership map, because a saved view must relight the range inputs it
was saved with (learning 39). The service validates condition fields
exactly as it validates value-filter fields (filterable and readable);
operators are guarded by the wire model's closed set, and stored values
stay un-revalidated per the standing ruling — replay goes through the
query service. Apply reconstructs the column state by grouping a
field's conditions (`gte`+`lte` fold into one range). The web omits an
empty `conditions` key on save, so membership-only views still save
against a server one version behind. Home tiles inherit the whole thing
for free: their numbers already replay saved components through
`gridStateFilters`.

## Abstentions

TUI abstains (ratified — consistent with the chooser and saved views).
MCP needs nothing: its search tools already accept every operator.

## Testing

Web lib: kind-to-condition mapping, the fold of `gte`/`lte` back into a
range, capture/apply round-trips carrying all three kinds. Component:
the range inputs stage and apply, clearing both sides releases the
column, the string toggle swaps modes, the funnel icon reads pressed
for every kind. Python: conditions round-trip both stores, service
refuses a condition on an unreadable or unfilterable field, the API
answers them back. One journey: range the invoice date, floor the
total, watch the rows and the footer agree, save it, read the Home
tile's count, click through, and find the range inputs relit. Pixel
check at desktop and 375px.
