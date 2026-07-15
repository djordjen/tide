# Query and Concurrency Contract

**Status: Structured keyset query and optimistic concurrency implemented for
the current headless adapters.**

## Query shape

Every query adapter produces the same `QuerySpec`. It contains a structured,
model-validated filter, an allow-listed sort, a bounded page size, and an
optional continuation cursor. Arbitrary SQL and unrestricted expression text
are never transport inputs.

Ordering must be deterministic. When the requested sort is not unique, the
query service appends the entity primary key as a tie-breaker. Continuation
cursors are opaque, versioned, and bound to the model, entity, normalized
filter, effective sort, page size, principal, and effective permissions. The
token itself contains no field values in readable form.

`RecordsService.query_page()` returns a `QueryPage` containing an immutable
record tuple and an optional `next_cursor`. `RecordsService.query()` remains a
list-returning compatibility wrapper. Clients obtain the next page by repeating
the same query shape with the returned cursor; changing a bound property or
presenting an unknown, expired, or tampered token fails with
`invalid_query_cursor`.

Pagination is keyset-based rather than offset-based. The repositories fetch one
extra record to determine whether another page exists and compare the effective
sort tuple against the stored boundary. Ascending order places nulls last;
descending order places nulls first. The SQL adapter emits bound lexicographic
predicates and never places boundary values in generated SQL text.

A cursor represents a continuation boundary, not a database snapshot. Keyset
pagination avoids offset drift when rows are inserted or removed before the
current position, but a concurrent update to a sort field can still move that
row between pages. Workflows that require a fixed historical result set need a
separate snapshot/export contract.

Relationship expansion is allow-listed and depth/size bounded. Counts, totals,
exports, and relationship loads apply the same row and field policies as the
root query. Collection hydration requires readable source-field access and
target-entity `read` access, and applies target `read` row criteria in the child
database statement. Adapters may impose smaller limits but may not bypass core
limits.

The default service boundary allows three collection levels and 1,000
authorized children per parent collection. An exceeded depth or item bound
fails with `relationship_expansion_limit`; it never returns a partial collection
that could be mistaken for complete data. A collection whose target entity is
not readable is projected as protected and is not queried by the SQL adapter.

The service validates stored fields, operators, filter value types, readable
field access, deterministic primary-key tie-breaking, and result limits. The
in-memory adapter evaluates the resulting repository query locally. The
SQLAlchemy adapter emits bound predicates for structured filters, stored-field
and reference-path policies, and single-collection aggregates, with ordering
and a dialect-specific bounded limit applied by the database.
Single-record read/update/action loads also include their applicable row-policy
criteria in the root SQL query.

Create policies evaluate the finalized candidate record before insertion.
Update policies are repeated in the atomic SQL `UPDATE` predicate, alongside
the identity and expected version, so a row that becomes unauthorized between
edit and commit cannot be changed. SQL distinguishes missing, policy-denied,
and stale rows without loading protected field values.

SQL relationship paths use correlated scalar subqueries. `count`, `sum`,
`average`, `min`, `max`, `any`, and `all` over one collection traversal use
correlated aggregate/`EXISTS` subqueries. Multiple collection traversals and
their policy translation remain pending and fail closed rather than falling
back to root-table post-filtering. For supported paths, target read criteria are
included in both hydration and root aggregate/reference predicates.

The default cursor store is thread-safe, process-local, bounded to 10,000
entries, and expires entries after 15 minutes. It is suitable for the current
single-process runtime. `CursorStore` is an explicit service dependency so a
shared deployment can later supply a durable or distributed implementation
without changing query or transport contracts. Restarting the process
invalidates cursors held by the default store.

## Mutation preconditions

An entity exposed for update or delete must have a concurrency token. Generated
REST responses expose that token through an ETag. Update, delete, and
record-targeted action requests require the version they observed; stale writes
fail without mutation.

TUI and MCP carry the same expected version through `RecordSession` and action
payloads. Missing, stale, null, and protected values are distinct states.

Initial conflict handling reports the current version and the fields that can
be safely disclosed. Interactive field-by-field merge remains a later feature.

Integer expected-version comparison and increment are executable in both the
in-memory and SQLAlchemy repositories. SQLAlchemy performs the comparison in
the `UPDATE` predicate so a stale mutation cannot overwrite the current row.

## Idempotency

Domain actions declare whether repeated execution is idempotent. Adapters may
accept an idempotency key for retryable mutations, but storage and replay occur
inside the secured application boundary. Reusing a key with a different
principal, action, target, or payload is rejected.

The invoicing `post` action is idempotent: retrying an already posted invoice
does not increment its version or repeat side effects.

The executable store contract writes an `in_progress` reservation before the
handler and completes it only after the secured record commit. Completed
replays reload and reauthorize the target rather than returning cached output.
Failed or crash-interrupted reservations reject automatic retries until an
operator reconciles them. Typed canonical fingerprints distinguish values such
as datetimes, decimals, and strings. See
[Action audit and idempotency](AUDIT-AND-IDEMPOTENCY.md).

## Errors and protected values

Transport errors map from stable application errors: invalid query, validation
failure, forbidden, not found, precondition required, stale version, and action
conflict. Public errors include a correlation identifier and safe field paths;
they never echo protected values or internal SQL details.
