# Legacy Databases

**Status: Compiler mapping contract implemented; SQLAlchemy adapter pending.**

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

At startup, the adapter will inspect the connected schema and compare it with
the compiled mapping. Missing objects, incompatible types, nullability that
cannot satisfy TIDE writes, and unsupported key shapes fail startup with
diagnostics. Inspection never repairs the database automatically.

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

## Initial compatibility boundary

Schema v0.1 still requires one declared primary-key field per entity. Composite
keys, database-generated key strategies, writable database views, stored
procedure mappings, trigger-driven refresh, and unusual vendor types need
explicit contracts before they can be claimed as supported.

The first adapter will prove the contract with SQLite and then PostgreSQL.
Other existing databases can be added through tested SQLAlchemy dialects;
support will be stated per dialect instead of assuming that every third-party
dialect has identical reflection, transaction, and type behavior.

A later `tide db inspect` command may propose TIDE metadata from an existing
schema. Generated proposals will remain reviewable source files and will never
change the inspected database.
