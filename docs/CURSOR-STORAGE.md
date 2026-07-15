# Shared Cursor Storage

**Status: Implemented for in-memory and SQLAlchemy-backed deployments.**

TIDE continuation cursors are opaque bearer tokens for secured keyset
pagination. The token identifies server-side state containing the model and
query shape, effective principal permissions, and the last typed sort values.
It is not an offset, a database snapshot, or a transport for readable field
values.

## Choosing a store

`RecordsService` uses `InMemoryCursorStore` by default. It is thread-safe,
bounded to 10,000 entries, and expires cursors after 15 minutes. This is the
simplest choice for tests and one-process local applications, but a process
restart invalidates every cursor and another process cannot resolve them.

`SQLAlchemyCursorStore` persists the same contract in the
`tide_query_cursor` table. Runtime instances that share the same operations
database can continue each other's pages and can survive restarts:

```python
from tide.data import SQLAlchemyCursorStore, SQLAlchemyRepository
from tide.services import RecordsService

repository = SQLAlchemyRepository(model, application_database_url)
cursor_store = SQLAlchemyCursorStore(
    operations_database_url,
    mode="managed",
    ttl_seconds=900,
    max_entries=10_000,
    max_state_bytes=65_536,
)
cursor_store.create_schema()  # explicit deployment/bootstrap step
cursor_store.validate_schema()

records = RecordsService(model, repository, cursor_store=cursor_store)
```

Normal application startup should call `validate_schema()`, not
`create_schema()`. Schema creation is an explicit deployment/bootstrap action.
Expired entries are removed while issuing cursors; operators may also call
`purge_expired()` as bounded housekeeping.

## Ownership and legacy databases

Constructing the store never emits DDL. Its default `mode="legacy"` uses and
validates an existing table but refuses `create_schema()`. `mode="managed"`
allows explicit creation of only the TIDE-owned cursor table and expiry index.

An externally owned application database remains under the legacy no-DDL
contract. Deploy the cursor store in a separately managed operations database
or schema when TIDE must not add tables to that database. Application records
and cursor state do not need to use the same SQLAlchemy engine.

## Security contract

The random token has at least 256 bits of entropy by default. TIDE stores only
its SHA-256 hash, so a read of the cursor table does not reveal usable bearer
tokens. A stolen token is still valid until expiry and must therefore travel
only over protected transports and stay out of logs.

Cursor state uses a non-executable, explicitly tagged JSON codec that preserves
integers, exact decimals, dates, datetimes, strings, nulls, booleans, and
finite floats. Deserialization never imports or executes stored objects.
Corrupted state fails with `cursor_store_error`; unknown, expired, tampered, or
query-mismatched tokens fail with `invalid_query_cursor`.

The table can contain protected filter and boundary values plus principal and
permission identifiers even though it does not contain raw tokens. Restrict
database, backup, diagnostic, and monitoring access accordingly. Capacity and
TTL are enforced transactionally, and each serialized state is bounded to 64
KiB by default. Shorter deployment-specific limits are appropriate when result
navigation does not need a 15-minute window.

Changing the model identity, query filters, effective ordering, page size,
principal, or effective permissions invalidates an old cursor at the service
boundary. Keyset continuation still reflects live database changes and does
not promise snapshot isolation across pages.
