# Query and Concurrency Contract

**Status: Structured query and optimistic concurrency partially implemented.**

## Query shape

Every query adapter produces the same `QuerySpec`. It contains a structured,
model-validated filter, an allow-listed sort, a bounded page size, and an
optional continuation cursor. Arbitrary SQL and unrestricted expression text
are never transport inputs.

Ordering must be deterministic. When the requested sort is not unique, the
query service appends the entity primary key as a tie-breaker. Continuation
cursors are opaque, versioned, bound to the effective sort/filter, and must not
contain unprotected field values in readable form.

Relationship expansion is allow-listed and depth/size bounded. Counts, totals,
exports, and relationship loads apply the same row and field policies as the
root query. Adapters may impose smaller limits but may not bypass core limits.

The current service validates filters, applies deterministic primary-key
tie-breaking, and bounds result counts. The in-memory adapter evaluates the
query locally. The initial SQLAlchemy adapter still uses that path; SQL
predicate, row-policy, ordering, and paging translation are the next required
slice.

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

## Errors and protected values

Transport errors map from stable application errors: invalid query, validation
failure, forbidden, not found, precondition required, stale version, and action
conflict. Public errors include a correlation identifier and safe field paths;
they never echo protected values or internal SQL details.
