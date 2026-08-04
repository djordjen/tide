# Record Audit and Action Idempotency

## Implemented boundary

`ActionService`, `RecordsService`, and `AuditHistoryService` share an
`ActionExecutionStore` for three related but distinct records:

- an idempotency reservation keyed by an adapter-supplied token; and
- an audit lifecycle row for each invocation of an action whose metadata has
  `audit: true` (the default); and
- a successful create/update/delete event containing safe changed-field
  metadata.

The default `InMemoryActionExecutionStore` preserves the fast headless test
contract. `SQLAlchemyActionExecutionStore` persists the same contract in
`tide_action_idempotency`, `tide_action_audit`, and `tide_record_audit` tables.
Construction never creates those tables. A TIDE-owned operations schema must
explicitly select `mode="managed"` and call `create_schema()`; the safe default
is `legacy`, where
DDL is refused and only compatibility validation is available.

```python
store = SQLAlchemyActionExecutionStore(repository.engine, mode="managed")
store.create_schema()
store.validate_schema()

records = RecordsService(model, repository, audit_store=store)
actions = ActionService(model, records, execution_store=store)
```

For a legacy application database, point the store at a separate TIDE-owned
database/schema when TIDE is responsible for its lifecycle. Do not create the
operations tables inside a third-party-owned schema merely because application
records are mapped there.

## Idempotency lifecycle

Keys are non-empty strings of at most 255 characters. They are globally unique
within one store. The request fingerprint binds the key to the principal,
entity, action, target identity, and typed payload. Canonical type tags ensure,
for example, that a datetime is not confused with a string containing the same
text. Roles and permissions are deliberately not captured in the fingerprint:
every completed replay loads and reauthorizes the current record under the
current `RequestContext`.

The lifecycle is:

```text
absent -> in_progress -> completed
                     `-> failed
```

The service checks a completed key before evaluating an action condition, so a
retry can replay an action whose successful state transition has since disabled
the command. A new reservation is written immediately before invoking the
handler. Validation, record authorization, disabled conditions, and missing
handlers therefore do not consume a key because no handler ran.

An ordinary handler or commit exception marks a claimed key `failed`. A process
or database interruption can leave it `in_progress`. Both states reject
automatic re-execution and require explicit reconciliation; TIDE never guesses
that a partly executed handler had no external effects.

## Audit lifecycle

An audited invocation writes `started` before handler execution and finishes as
one of:

- `succeeded`;
- `replayed`;
- `conflict`; or
- `failed`.

Rows contain the principal, channel, entity/action, typed target identity,
correlation identifier, timestamps, outcome, and safe error code. They do not
contain request payloads, protected field values, credentials, SQL parameters,
or the raw idempotency key. When a key exists, audit stores only its SHA-256
hash for operational correlation. `audit: false` suppresses the audit row but
does not disable idempotency storage. The SQL store assigns an identity-backed
sequence when each audit row begins, so equal database timestamps do not make
invocation history depend on random event identifiers.

## CRUD change events

Every successful root `RecordsService` create, update, or delete writes a
record event after the repository mutation. The event contains the typed
identity, operation, mutation source (`user`, `action`, or `system`), principal,
channel, correlation identifier, timestamp, and changed fields. An update made
inside a domain action therefore shares that action's correlation identifier.
Failed validation, authorization, concurrency, and repository operations do not
write success events.

A delete also writes one event per row removed by a cascade. The repository
reports what it actually touched rather than the service inferring it from the
copy it loaded, because that copy is depth- and policy-bounded and would
under-report. Cascaded rows are audited under the deleting principal, whose
authority over the target record is what permitted their removal; rows a
`set_null` reference merely rewrites are not yet recorded.

Field metadata controls detail capture:

- `audit: changes` (default) stores only the field name;
- `audit: values` stores bounded before/after values for supported scalar and
  reference fields; and
- `audit: none` omits the field.

One encoded field value is limited to 4096 bytes. Collections, unsupported
objects, and oversized values fall back to field-name-only capture. A field or
computed dependency governed by any read policy is redacted before persistence,
even when `values` was requested. `AuditHistoryService` then rechecks the
current reader's field permissions and redacts any stored value that the reader
cannot currently access. The SQL store hashes serialized identities for its
record lookup index but still compares the exact typed identity to reject hash
collisions.

## Secured record history

`AuditHistoryService` is the read-only boundary used by renderers and HTTP
adapters. Access requires an explicit `permissions.audit` entry on the entity
and a matching grant in the current principal's role. Omission denies access.
Queries are scoped to one typed entity identity, newest first, and accept a
bounded limit from 1 through 500. SQLAlchemy applies the entity, serialized
identity, ordering, and limit in SQL rather than loading the whole audit table.

FastAPI exposes history only for entities that also expose REST `get`, at
`<record-resource>/{identity}/_audit`. The authenticated session contract tells
remote renderers only whether audit access is available; it does not disclose
permission names. Textual uses the same local/remote reader contract and shows
**History** only when authorized.

The wire and TUI projections combine action lifecycle and successful CRUD
events, while deliberately omitting payloads, protected values, credentials,
raw idempotency keys, and even the stored idempotency-key hash. Equal timestamp
ticks use an explicit lifecycle phase order so an action completion sorts after
its correlated record write rather than depending on random event identifiers.

## Crash and transaction semantics

The action reservation is durable before the handler runs, which provides a
fail-closed, at-most-once retry posture. The current action store and application
record write use separate short database transactions. A crash after the record
commit but before the reservation is completed therefore leaves an
`in_progress` record rather than risking duplicate execution. Operators must
compare the audit correlation, target state, and application-specific side
effects before reconciling it.

CRUD events are written **inside** the record's own write transaction. The
repository invokes a callback on its connection before committing, and the
audit store enlists in that connection when it belongs to the same engine, so
a change and the record of it commit together or not at all. An audit write
that fails takes the change down with it: an unaccountable change is the one
outcome an audit trail exists to prevent. A store configured against a
different database cannot enlist and opens its own transaction, which restores
the older gap — deployments that separate the two should expect it.

Action reservations are a separate story and still use their own short
transaction, so the `in_progress` reconciliation above still applies to them.

Retention and purge policy, reconciliation commands, collection-detail events,
failed CRUD-attempt audit, auditing of MCP/report/export operations, and a unit
of work spanning several record writes remain later production work.
