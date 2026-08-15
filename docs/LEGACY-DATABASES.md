# Legacy Databases

**Status: Compiler, SQLAlchemy Core adapter and schema inspection implemented.**

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
| compatibility diff | safety-classified migration proposal | read-only report (`tide db diff`) |
| create/revision/migrate | explicitly invoked | refused |
| application reads/writes | mapped services | mapped services |

The no-DDL rule does not make data access unrestricted. Entity, row, field,
action, and reference validation still pass through the same secured
application services.

`tide db diff --json` labels legacy output as a `compatibility_report`, marks
every incompatibility `manual`, sets `revision_blocked`, and reports that
revision/apply commands are unavailable. It does not inspect or require
TIDE-owned runtime-state tables in the externally owned schema.

`migration_id` may document stable logical identity, but `renamed_from` is
rejected in legacy mode. A rename declaration would imply TIDE-owned schema
intent, while the external database owner remains the sole authority for all
physical renames and other DDL.

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
keys, writable database views, stored procedure mappings, trigger-driven
refresh, and vendor types beyond the table above need explicit contracts before
they can be claimed as supported. `tide db inspect` names the tables it skipped
for these reasons rather than proposing something that would not work.

Database-generated keys are supported for the two shapes that arise in
practice: an identity column, whose value is read back after the insert, and a
`uuid` key with a declared `server_default`.

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

## Adopting an existing schema

Explicit mapping is the right rule and it is also the reason adopting a large
schema by hand is tedious enough to stop people trying. `tide db inspect`
turns one reflection pass into reviewable source files:

```bash
export TIDE_DATABASE_URL='sqlite+pysqlite:///./legacy.db'
uv run tide db inspect --database-env --output ./legacy-crm --application "Legacy CRM"
```

The URL comes from the environment, never from the command line, like every
other `tide db` command. `--schema` selects a schema, and without `--output`
the proposal is printed instead of written.

## Choosing which tables to adopt

An existing database usually holds more than one application's worth of
tables, and TIDE has no business mapping the audit trails, staging tables and
integration spool of the product that owns the schema. `--list` shows what is
there and what each table would become, without writing anything:

```bash
uv run tide db inspect --database-env --list
```

```text
CUSTOMER_MASTER  propose legacy.CustomerMaster (5 fields)
EMPLOYEE_MASTER  propose legacy.EmployeeMaster (2 fields)
NOTE_LOG         skip -- table declares no primary key
ORDER_LINE       skip -- composite primary key (ORDER_NO, LINE_NO); schema v0.1 maps one key column

2 of 4 object(s) would be proposed. Re-run without --list to write them.
```

`--table` and `--exclude` both take an exact name or a glob pattern and both
repeat, so a schema with a naming convention can be adopted by convention:

```bash
uv run tide db inspect --database-env --table 'ERP_*' --exclude '*_AUDIT' --list
```

Patterns match case-insensitively, and identically on every platform —
`fnmatch` alone would fold case according to the host operating system, which
would make one command select different tables on Windows and on Linux.
Exclusions are applied after selection. A `--table` pattern that matches
nothing stops the command before anything is written, because the alternative
is a project that is quietly smaller than the one that was asked for:

```text
Database inspection failed: no table matches 'CUSTMER_*'
```

Selecting a subset changes what a foreign key can mean. A column pointing at a
table outside the proposal cannot become a reference — that entity does not
exist, and the model would not compile — so it keeps its physical mapping and
loses only the navigation, and the command says which:

```text
Reference dropped -- CUSTOMER_MASTER.OWNER_EMPLOYEE_NO: EMPLOYEE_MASTER is not in this proposal, so the column is mapped without its reference
```

The same applies without any selection at all, when the table on the other end
is one schema v0.1 cannot map. Resolution runs after every selected table has
been planned, so a foreign key is judged against the entities the proposal will
actually contain rather than against the tables the database happens to hold.

It proposes one entity per table, mapping each column explicitly:

```yaml
entity: legacy.CustomerMaster
storage: {table: CUSTOMER_MASTER}
display: display_name
fields:
  customer_no: {type: integer, primary_key: true, column: CUSTOMER_NO}
  display_name: {type: string, length: 120, required: true, column: DISPLAY_NAME}
  credit_limit: {type: decimal, precision: 12, scale: 2, column: CREDIT_LIMIT}
  is_active: {type: boolean, required: true, column: IS_ACTIVE}
  owner_employee_no: {type: reference, target: legacy.EmployeeMaster, storage: OWNER_EMPLOYEE_NO, on_delete: restrict}
```

### The names it chooses

A reflected name is snake-cased and the physical one is kept beside it in
`column:`. `SerialNo` becomes `serial_no`; `CUSTOMER_NO` was already
`customer_no` and does not move.

The field name is TIDE's to choose precisely because `column:` is what binds it
to the table — nothing downstream reads the name back. It is spent in four
places instead: the REST payload key, the MCP argument name, the YAML you edit,
and the label every surface shows. That last one is why the boundary between
words has to survive reflection. No proposed field carries an explicit
`label:`; `humanize` derives one by splitting the name and titling it, so the
capitals are the only record of where the words divide, and lowercasing the run
destroys it for good — `serialno` reads `Serialno` on all four surfaces and no
layer downstream can put the boundary back.

| Column | Field | Label |
|---|---|---|
| `SerialNo` | `serial_no` | Serial No |
| `ReplacedBy` | `replaced_by` | Replaced By |
| `ModelName` | `model_name` | Model Name |
| `CUSTOMER_NO` | `customer_no` | Customer No |

An acronym is the case this reads poorly: `UniqueID` becomes `unique_id` and
labels as `Unique Id`, `GCRecord` labels as `Gc Record`. Write `label: Unique
ID` on the field to say otherwise — a declared label is used exactly as
written, and the proposal is source you own. Renaming the field itself is safe
too, as long as `column:` stays; `validate_schema()` is what proves the
mapping, and it reads the column.

Entity names keep their capitals, and everything derived from one is split the
same way, so a generated project holds one convention rather than two:

```text
legacy.EquipmentInstance    entity
models/equipment_instance.yaml            file
views/equipment_instance-browse.yaml      view file
legacy.equipment_instance.list            permission
```

What it cannot map it reports on stderr rather than guessing, so redirecting
the proposal still leaves you holding the list of what is missing from it:

```text
Not proposed -- NOTE_LOG: table declares no primary key
Not proposed -- ORDER_LINE: composite primary key (ORDER_NO, LINE_NO); schema v0.1 maps one key column
```

The closing summary counts every category, so a run that dropped something is
not one you have to notice:

```text
Proposed 1 entity in legacy-crm; 0 object(s) not proposed, 1 reference(s) dropped, 3 table(s) not selected. Review the files, then: tide model validate legacy-crm
```

The output is a starting point, not an application: it carries no `on_delete`
judgement beyond `restrict`, which is the safe default rather than an
observation about the data. Read it, then:

```bash
uv run tide model validate ./legacy-crm
```

## Compiling is not running

A proposal without `--runnable` is metadata. It compiles, and
`validate_schema()` confirms it matches the database — and no surface can open
it, because its entities are exposed to no channel, hold no permissions, and
the application declares no views and no roles. The TUI refuses such a model
outright with `application does not define a browse view`.

`--runnable` proposes the rest of what an application needs:

```bash
uv run tide db inspect --database-env --runnable --output ./legacy-crm --application "Legacy CRM"
```

| Added | Per |
|---|---|
| `expose` for the TUI, REST and MCP, and five `permissions` | entity |
| a `browse`, an `edit` and a `lookup` view | entity |
| `editor: lookup` and `lookup_view` on each reference | reference |
| a `collection` field, an `inline_edit` view and a form section | reference, seen from the entity it points at |
| `security/policies.yaml` granting every permission to one role | application |
| `views:` and `security:` paths | `tide.yaml` |

All three surfaces, not just the terminal: the Web UI is a REST client, so an
application exposed only to the TUI answers 404 to every request the browser
makes and renders as an empty application. MCP is exposed in the metadata but
still requires `tide serve --mcp` before anything is served.

The role is `operator` unless `--role` names another. It is granted everything,
because which operations an account should have is a decision about the
business rather than something a reflection pass can observe — the permissions
are declared separately per operation so any of them can be taken away.

The reference wiring is not decoration, and it is two settings rather than one.
Without `lookup_view` the form answers `No lookup view is configured for …` and
the value cannot be set at all; without `editor: lookup` the field renders as a
select over the first 500 rows of the target table, which is both useless on a
legacy table and fatal — a stored key outside that window raises
`InvalidSelectValueError` and takes the whole screen down.

Browse and lookup views carry every field, display column first. A wide legacy
table makes a wide view, which is easier to read and delete from than a short
one is to spot the omissions in.

## Both halves of a relationship

Reflection finds only the half of a relationship that holds the column, so a
proposal built from foreign keys alone gives every child a picker and every
parent nothing — no way to see, from a record, what points at it.
`--runnable` turns each reference around and gives the entity it points at a
collection, an inline row editor, and a section on its form. A table pointed
at twice, or one that points at itself, keeps the key in the name:

```yaml
equipment_instance_replaced_by: {type: collection, target: legacy.EquipmentInstance, inverse: replaced_by}
equipment_instance_replaced: {type: collection, target: legacy.EquipmentInstance, inverse: replaced}
equipment_instances_tasks: {type: collection, target: legacy.EquipmentInstancesTasks, inverse: equipment_instances}
```

This is what makes an XPO- or XAF-style many-to-many table usable. Those
intermediate tables carry their own surrogate key, so schema v0.1 maps them as
ordinary entities with two references — and the collection synthesized from
each reference is what puts them on both parents' forms.

Collections are loaded eagerly and with no cycle guard, so the graph they make
has to stay a shallow acyclic one. Three shapes are declined and named on
stderr rather than proposed:

```text
Collection not proposed -- legacy.EquipmentInstance.replaced_by: a collection of its own entity would be hydrated into itself without end
```

| Declined | Because |
|---|---|
| a table pointing at itself | the collection would be hydrated into itself for ever |
| a key closing a cycle back to the owner | the same, one hop further out |
| a chain longer than `RelationshipLoadPlan.max_depth` | past it the repository raises `RelationshipExpansionLimit` |

The last one is not a slow list, it is an empty one: hydration refuses rather
than truncating, so a single over-long chain breaks the browse for the entity
at its head. The limit is read from the load plan rather than restated here.
The reference itself always survives — only the collection turned around from
it is declined, so nothing is lost from the record that holds the key.

**The Textual form renders the first collection only.** Its record screen
resolves one collection and builds the table, line editor and action bar
around it; the others are declared, exposed and served, and the Web UI shows
them all. Nothing is dropped from the metadata, and the terminal shows one
until its form screen learns to hold several.

A proposal that compiles is not yet a proposal that fits, so the test covering
this runs `validate_schema()` against the same database the metadata was read
from. That is the property worth having: a model that compiles but does not
match its source schema would be worse than no model, because it looks
finished.

Inspection is read-only in the strongest sense the adapter offers -- connect,
reflect, disconnect -- and a test asserts that no statement it issues begins
with `CREATE`, `ALTER`, `DROP`, `INSERT`, `UPDATE` or `DELETE`. It also refuses
to overwrite files a previous run produced, so a second inspection cannot
discard hand edits.

## Vendor types

Two SQL Server spellings are mapped rather than treated as foreign:

| Column type | Field |
|---|---|
| `money` | `decimal` precision 19, scale 4 |
| `smallmoney` | `decimal` precision 10, scale 4 |
| `uniqueidentifier` | `uuid` |

`money` is a storage type, not a semantic one, so it stays out of portable
metadata: the same YAML still deploys against SQLite. The capacities are stated
in one place that both the inspector and `validate_schema()` read, so a
proposal cannot suggest a decimal that validation would then reject. A field
wider than the column still fails -- `decimal` with scale 6 does not fit a
money column, and the check says so.

`uuid` is a declared field type because a GUID key needs to reach both
supported dialects from one declaration: `UNIQUEIDENTIFIER` on SQL Server,
`CHAR(32)` on SQLite. TIDE generates the key itself before the insert, so a
master-detail write can point child rows at a parent that does not exist yet.
A field declaring a `server_default` defers to the database instead, which is
how a legacy `NEWSEQUENTIALID()` column keeps its sequential keys and the
clustered index that random values would fragment.
