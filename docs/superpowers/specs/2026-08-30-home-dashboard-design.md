# Home dashboard

Date: 2026-08-30. Status: approved (personal-only — assembled, not
declared; live counts — nothing persisted, no staleness contract).

## What

The web gains a **Home**: the landing surface a person's own work
assembles itself onto. Three bands, every brick already paid for:

- **My views** — every saved view the principal keeps, across all
  browses, each tile carrying its live numbers ("My drafts — 5 ·
  2,952.50") and opening its browse with the whole state relit.
- **Workspaces** — one tile per navigation item: the label and the live
  numbers of the unfiltered browse, opening it.
- **Reports** — the capability-filtered summary reports, opening the
  existing preview in place.

XAF's DashboardView, taken the personalization way: the *application*
declares nothing new, the *person's* accumulated state is the dashboard.
A declared `dashboard:` block (the application's official home) is a
separate future decision, deliberately not taken today.

## Routing

Home is the second framework destination after `_tide.administration`,
traveling in the same `view` parameter as `_tide.home` — an underscore
name a compiled view can never hold. It becomes the **default landing**:
the `view` parameter's fallback, so Home keeps the clean URL and every
real view writes `?view=…`. An address-bar view the manifest does not
know now falls back to Home rather than the first navigation item.
Identity-only principals keep landing on administration exactly as
before. The sidebar gets a Home entry above the navigation groups
(framework chrome, like Identities below them); the phone select gets
the same option.

Opening a saved view from a tile is shell state, not URL state: the
shell hands the workspace a one-shot "open with saved view" instruction
consumed after the saved-views query lands. A deep-linkable
`?saved=Name` is deliberately out — a URL contract over user-chosen
names is its own decision.

## Live numbers — one composition rule

A tile's numbers are the browse's own: the view's declared summaries,
guaranteed to include a count (when the declaration carries none, a
`count` over the identity field is added), evaluated by the ordinary
`_query` door with `limit: 1` under the tile's filters. Saved-view tiles
replay the saved components; workspace tiles ask unfiltered. The
named-filter-plus-value-filters composition the browse already performs
moves into one shared pure function both callers use — two spellings of
"what constrains this grid" is the defect class learning 34 exists for.
Numbers load lazily per tile and are never stored.

## The one new server door

`GET /_tide/saved-views` (no view segment) answers every saved view the
principal keeps, each entry carrying its `view` name, filtered to views
that still exist as browses. The rows contract gains `list_mine`
(in-memory and SQLAlchemy alike, ordered by view then name), the service
stays the owner of what a view name must be, and the web treats a 404 as
"feature absent" exactly as the per-view listing does. The client
additionally intersects with the manifest: a tile is offered only for a
view this principal can currently list. MCP abstains; the TUI abstains
(it has no home surface).

## Testing

Service/store: `list_mine` answers across views in view-then-name order,
in both stores; entries for a view that no longer exists are dropped.
REST: the catalogue answers with view names attached; 401 without a
token. Web: vitest for the shared filter composition and the
ensure-count rule; a journey that saves a view, lands on Home, reads the
tile's live count, clicks it, and finds the browse open with the saved
view active and its funnel lit; the workspace tiles and report shortcuts
asserted on the same journey. 375px verified against the built bundle.
