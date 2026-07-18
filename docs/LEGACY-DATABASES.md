# Legacy Databases

**Status: Initial compiler and SQLAlchemy Core adapter implemented.**

Legacy mode lets a TIDE application use tables owned by another product or
team without changing their structure. It is intended for existing databases,
shared integration databases, and schemas whose migration lifecycle must stay
outside TIDE.

## Ownership boundary

The application opts in explicitly:

```yaml
# tide.yaml
schema_version: "0.1"
application: {name: Legacy CRM, version: 0.1.0}
database: {mode: legacy}
model: {paths: [models]}
```

Legacy mode imposes a hard no-DDL rule. The persistence adapter must not call
`create_all`, execute Alembic revisions, or issue `CREATE`, `ALTER`, `DROP`, or
other schema-changing statements. Database connection URLs and credentials
remain deployment configuration and are not stored in portable metadata.

The adapter's `validate_schema()` method inspects the connected schema and
compares it with the compiled mapping. Missing objects, incompatible type
families or capacities, nullability that cannot satisfy TIDE writes, unmapped
required columns, and unsupported key shapes fail with structured issues.
Inspection never repairs the database automatically; deployment startup must
call this validation before becoming ready.

## Explicit physical mapping

Every legacy entity names its existing table and optionally its schema:

```yaml
entity: legacy.Customer
storage:
  schema: erp
  table: CUSTOMER_MASTER

display: name
fields:
  id:
    type: integer
    primary_key: true
    column: CUSTOMER_NO

  name:
    type: string
    length: 120
    required: true
    column: DISPLAY_NAME

  account_manager:
    type: reference
    target: legacy.Employee
    storage: OWNER_EMPLOYEE_NO
    on_delete: restrict
```

Scalar and stored-computed fields use `column`. References use the existing
foreign-key column through `storage`. Collections and virtual computed fields
are navigation or runtime values and have no column mapping.

The compiler requires explicit mappings in legacy mode. This prevents a naming
convention change from silently selecting the wrong table or column.

Construction and validation are explicit:

```python
model = compile_project("applications/legacy-crm")
repository = SQLAlchemyRepository(model, deployment_database_url)
repository.validate_schema()
records = RecordsService(model, repository)
```

Constructing the repository emits no DDL. Calling `create_schema()` in legacy
mode raises `SchemaManagementError` before issuing SQL.

## Schema commands

Database commands have different authority by mode:

| Command | Managed mode | Legacy mode |
|---|---|---|
| schema inspection | allowed | allowed |
| compatibility diff | migration proposal | read-only report |
| create/revision/migrate | explicitly invoked | refused |
| application reads/writes | mapped services | mapped services |

The no-DDL rule does not make data access unrestricted. Entity, row, field,
action, and reference validation still pass through the same secured
application services.

Metadata-defined deletion also preserves the no-DDL boundary. The SQLAlchemy
repository checks `restrict` references and executes declared `cascade` or
`set_null` behavior inside the application transaction, so correctness does not
depend on TIDE adding or changing a foreign key. Existing database constraints
and triggers still apply; an integrity rejection is returned as the stable
`delete_restricted` conflict. Version preconditions are required only when the
mapped entity already declares a concurrency-token field.

Durable action audit/idempotency and shared query-cursor tables are TIDE-owned
operational data. They are not silently added to a legacy application schema.
Both SQLAlchemy operational stores default to `mode="legacy"` and refuse
`create_schema()`; deploy a separate explicitly managed operations database or
schema when TIDE should own those tables.

## Initial compatibility boundary

Schema v0.1 still requires one declared primary-key field per entity. Composite
keys, database-generated key strategies, writable database views, stored
procedure mappings, trigger-driven refresh, and unusual vendor types need
explicit contracts before they can be claimed as supported.

The adapter and no-DDL behavior are currently proven live with SQLite.
Microsoft SQL Server is the first additional target: schema and query
compilation are covered and an opt-in live integration suite is available.
Support is stated per dialect instead of assuming that every database has
identical reflection, transaction, identity, and type behavior.

Root structured filters, direct/reference row policies, single-collection
aggregates, ordering, and limits use bound SQL in legacy mode as well.
Policy-aware collection hydration uses bound target-row predicates and performs
no DDL. Multiple-collection policies remain outside the implemented production
boundary.

A later `tide db inspect` command may propose TIDE metadata from an existing
schema. Generated proposals will remain reviewable source files and will never
change the inspected database.
