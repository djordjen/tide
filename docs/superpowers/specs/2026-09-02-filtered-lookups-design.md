# Filtered lookups (static criteria) — design

Ratified 2026-09-02. XAF's `DataSourceCriteria`, scoped by Djordje's ruling:
**static first** — a criterion over the target entity only, with the syntax
leaving room for draft-dependent criteria later.

## The declaration

A reference field may declare one boolean expression over its **target**
entity's fields:

```yaml
product:
  type: reference
  target: catalog.Product
  lookup_view: catalog.Product.lookup
  lookup_filter: active == true
```

- One string, the existing expression language, validated at compile time
  against the **target** entity (not the owner).
- `$` parameters are refused at compile time — that is the static gate, and
  the door left open: a later slice can admit `$this.<field>` bindings
  without changing the key or the grammar.
- No globals either: the criterion sees exactly what `immutable_when`
  sees — the (target) entity's fields and literals. A `today`-style global
  does not exist in the entity expression language, so date-window
  eligibility stays out until the language itself gains one.
- Anything the expression language can translate for a row policy it can
  translate here; there is deliberately no second grammar.

## One rule, resolved in one place, enforced at two kinds of door

`RecordsService.lookup_criteria(owner_entity, field_name)` is the single
resolver: it validates the edge (field exists, is a reference) and returns
the declared expression as a criteria tuple — `()` when nothing is declared.
Nobody else reads the metadata key.

### Pickers narrow (read side)

- `QuerySpec` gains `criteria: tuple[str, ...] = ()` — expression strings,
  service-internal, never client-authored. `query_page` appends them to the
  `row_criteria` handed to the repository, so both repositories apply them
  exactly as they already apply row-policy criteria (SQL via
  `translate_expression`, memory via `evaluate_expression`). No repository
  changes.
- Criteria fields are **not** run through `_require_queryable_field`: like a
  row policy, the criterion may name target fields the requester cannot
  read. The rule is the model's, not the caller's.
- `CursorShape` pins `criteria`, so a page-2 cursor cannot drop or invent
  the filter. Pre-deploy cursors invalidate cleanly via the existing shape
  mismatch.
- The summaries path receives the same criteria (a summarized picker page
  must count what the page shows), threaded the same way.
- `lookup_records` gains `criteria: tuple[str, ...] = ()` and threads it
  into each QuerySpec it builds. The global-search fan-out passes nothing —
  search is entity-level; the filter is a property of the edge.

### Writes refuse (the authority)

A new commit step beside `_validate_entity` in the issues list:
`_reference_filter_issues(entity, session, values)`.

- Fires only when the reference value is **newly set or changed**: on
  create, any non-null value; on update, a value differing from
  `session.original`. Collection children are walked the way
  `_validate_entity` walks them, each child matched to its original sibling
  by child primary key (a child without one is new).
- The target row is loaded **model-level** (no row criteria — eligibility is
  a model fact, not a visibility fact), batched per target entity. A target
  that does not load keeps today's behaviour (SQL FK speaks, memory stays
  lenient) — this slice does not become an existence oracle (C10 stays
  deferred).
- Refusal is an ordinary `ValidationIssue("lookup_filter",
  "<field> references a row its lookup filter excludes", (field,))`,
  severity `error` — it rides the existing envelope on every surface.
- **Unchanged values never re-fire.** An invoice holding a line whose
  product has since been retired stays editable; a stored reference TIDE
  did not write renders as stored. This is deliberately stricter than XAF
  (which filters the UI only) and deliberately weaker than a validation
  rule (which would re-refuse history): the criterion gates the *choosing*
  moment.
- Enforcement is source-blind (USER and ACTION writes alike): the rule is
  the application's own; a handler needing an exception is a design
  conversation, not a bypass.

## Wire and surfaces

### REST

`TideQueryInput` (extra="forbid") gains
`lookup_source: {entity, field} | null`. The `_query` handler resolves it
through `lookup_criteria` after checking the named field is a reference
targeting the queried entity — anything else is a 400 naming the problem.
No declared filter → no-op, so clients may send the source for unfiltered
references harmlessly.

### Web

`TidePresentationLookup` gains `source: {entity, field} | null`, present
**only when the reference declares a filter**. The dialog echoes it
verbatim as `lookup_source` on every `_query` it issues. Skew table:

| client \ server | old server            | new server                  |
|-----------------|-----------------------|-----------------------------|
| old client      | today                 | unfiltered picker; write path refuses ineligible picks |
| new client      | no `source` in manifest → nothing sent → no 422 | filtered picker |

Stored-but-ineligible display already tolerates: `TideDisplayValue` falls
back to GET-by-id and then to the raw key. No change, one pinning test.

### TUI

- `LookupScreen` resolves the edge through `lookup_criteria` and passes it
  to `lookup_records`.
- The select-style editor's option load applies the criteria — which
  re-keys the option cache by **field name** (two references to one target
  may filter differently).
- The select editor additionally gains **stored-option injection**: when
  the stored key is not among the fetched options, the row is fetched and
  appended (falling back to the raw key as its own label). This closes the
  documented Textual `InvalidSelectValueError` screen-crash for stored keys
  outside the 500-row window (docs/LEGACY-DATABASES.md, DECISIONS.md) —
  the filter would otherwise add a second, YAML-reachable route to a known
  crash. The docs describing the defect are updated with it.

### MCP

Abstains, deliberately: the search tools are entity queries an agent
composes freely, and an ineligible pick is refused at write with the rule
named in the error. No tool argument.

## Reference application

`sales.InvoiceLine.product` gains `lookup_filter: active == true`
(`catalog.Product.active` has existed since the first commit, default
true). Demo seed data is unchanged — both seeded products are active, and
the e2e journey creates and retires its own product (unique-named state,
per the journeys doctrine), so no seeded-count pins move.

## Deliberate outs (DECISIONS row mirrors these)

- Draft-dependent criteria (`$this.<field>`) — the second half of XAF's
  DataSourceCriteriaProperty; needs draft values on the wire.
- Early refusal in `apply_reference_selection` — commit is the authority;
  the picker never offers ineligible rows, so only hand-composed REST
  calls reach it late.
- Funnel checklists / `distinct_values` narrowing — funnels enumerate
  stored data, not eligibility.
- `tide db inspect` stubs — the inspector cannot know business rules.
- A `lookup_filter` message override — the derived message suffices until
  someone asks.

## Testing

Cross-layer suite `tests/test_lookup_filters.py` in the soft-validation
mold: compiler diagnostics (TIDE293 structural, TIDE30x expression issues at
the field path, parameters refused); service matrix over both repositories
(filtered page, cursor page-2 keeps the filter, criteria fields unreadable
by the requester still filter); write-path matrix (create refused /
changed refused / unchanged tolerated / child rows per-row); REST door
(filtered query, 400s, no-source = unfiltered); TUI pilots (filtered
options, stored-injection renders instead of crashing); web vitest (dialog
sends the echoed source, excluded row absent, no source → nothing sent) and
one e2e journey (create product, retire it, open the line dialog, assert
absence). Sabotage the gate before trusting it, per the rails.
