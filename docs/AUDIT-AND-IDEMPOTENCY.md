# Action Audit and Idempotency

## Implemented boundary

`ActionService` uses an `ActionExecutionStore` for two related but distinct
records:

- an idempotency reservation keyed by an adapter-supplied token; and
- an audit lifecycle row for each invocation of an action whose metadata has
  `audit: true` (the default).

The default `InMemoryActionExecutionStore` preserves the fast headless test
contract. `SQLAlchemyActionExecutionStore` persists the same contract in
`tide_action_idempotency` and `tide_action_audit` tables. Construction never
creates those tables. A TIDE-owned operations schema must explicitly select
`mode="managed"` and call `create_schema()`; the safe default is `legacy`, where
DDL is refused and only compatibility validation is available.

```python
store = SQLAlchemyActionExecutionStore(repository.engine, mode="managed")
store.create_schema()
store.validate_schema()

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

## Crash and transaction semantics

The reservation is durable before the handler runs, which provides a
fail-closed, at-most-once retry posture. The current action store and application
record write use separate short database transactions. A crash after the record
commit but before the reservation is completed therefore leaves an
`in_progress` record rather than risking duplicate execution. Operators must
compare the audit correlation, target state, and application-specific side
effects before reconciling it.

Atomic completion in the same transaction as application changes, retention
and purge policy, reconciliation commands, protected change-detail capture, and
auditing of generic CRUD/MCP/report operations remain later production work.
