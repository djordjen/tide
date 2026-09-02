# Application Model

## Purpose

The application model is TIDE's central dictionary. It describes domain
structure and application intent once so that persistence, views, REST, MCP,
reports, validation, and security can share the same meaning.

YAML is the preferred human authoring format. JSON may be accepted and
exported, but both compile into the same normalized `ApplicationModel`.

The source-schema version is separate from the application version:

```yaml
schema_version: "0.1"
application: {name: Invoicing, version: 0.1.0}
```

See [Metadata contract v0.1](METADATA-V0.md) for the strict parsing,
diagnostic, path-confinement, and currently executable semantic rules.

## Application organization

A repository containing both TIDE and applications is expected to resemble:

```text
src/tide/                  TIDE runtime and compiler
applications/
    invoicing/             one application root
        tide.yaml
        models/
            crm/
                customer.yaml
            sales/
                invoice.yaml
                invoice_line.yaml
        views/
        presentation/
            defaults.yaml
            formats.yaml
            presets.yaml
        actions.py
        reports/
        security/
        migrations/
        tests/
```

Every direct child of `applications/` is independent and contains its own
`tide.yaml`. Paths in that manifest are confined to that application root. The
runtime may be installed separately; the `applications/` convention does not
couple application source into the framework package.

Portable model files do not contain production credentials or a fixed database
URL. Deployment-specific database settings, secrets, logging, and environment
choices belong in environment variables or deployment configuration.

## Application navigation

`presentation/defaults.yaml` may define the portable application map as
ordered groups of browse views:

```yaml
navigation:
  - label: Sales
    items:
      - view: sales.Invoice.browse
  - label: Master Data
    items:
      - view: crm.Customer.browse
      - view: catalog.Product.browse
```

The compiler resolves this into immutable `NavigationGroup` and
`NavigationItem` values inside `ApplicationModel`. An item label may override
the entity label; otherwise it is derived from that entity. Renderers consume
the normalized values, intersect them with authenticated list capabilities,
and remove empty groups. Textual and Web therefore share the same
portable navigation without treating menu visibility as authorization.

## Database ownership modes

The application manifest states who owns the physical schema. TIDE-managed
applications use the default mode:

```yaml
database: {mode: managed}
```

An application that maps a database created and evolved by another system must
opt into legacy mode:

```yaml
database: {mode: legacy}
```

Legacy mode is a no-DDL boundary. TIDE may inspect the connected database and
read or write mapped records, but it must never create, alter, drop, or migrate
database objects. Startup fails with compatibility diagnostics when required
objects do not match the compiled model.

Physical names are explicit in legacy mode:

```yaml
entity: legacy.Customer
storage: {schema: erp, table: CUSTOMER_MASTER}

fields:
  id:   {type: integer, primary_key: true, column: CUSTOMER_NO}
  name: {type: string, length: 120, column: DISPLAY_NAME}
```

References continue to use `storage` for their physical foreign-key column,
while scalar fields use `column`. Collections and virtual computed fields have
no physical column. See [Legacy databases](LEGACY-DATABASES.md) for the adapter
contract and current limitations.

## Compact field syntax

YAML flow mappings keep simple fields readable while complex definitions remain
expanded:

```yaml
entity: crm.Person
label: People
display: "{first_name} {last_name}"

expose:
  tui: true
  rest: {operations: [list, get, create, update, delete]}
  mcp:
    resources: [schema, record, audit]
    tools: [search, create, update, delete]

fields:
  id:         {type: integer, primary_key: true}
  first_name: {type: string, length: 80, required: true}
  last_name:  {type: string, length: 80, required: true}
  birth_date: {type: date}
  email:      {type: string, length: 254,
               edit_mask: {regex: '[^\s@]+@[^\s@]+\.[^\s@]+'}}
  active:     {type: boolean, default: true}
```

Mutation exposure grants no authority by itself. Every exposed operation also
needs its entity permission, such as `permissions.delete`, and deletion follows
each incoming reference's explicit `on_delete` behavior.

Record-history access is separately fail-closed. An entity may declare
`permissions.audit`; only principals with that permission can use renderer or
REST history surfaces. The permission does not imply ordinary record mutation,
and ordinary read/update permissions do not imply audit access.

Each field also accepts `audit: none | changes | values`. The default
`changes` records only that the field changed. `values` opts a scalar/reference
field into bounded before/after capture, while `none` omits it. Collection or
oversized values fall back to field-only capture. Any field (or computed
dependency) with a read policy is redacted before storage, and history services
recheck the current reader's field permissions before returning stored values.

The field identifier is stable application vocabulary. Labels, help text,
formats, editor hints, and localization are separate facets of the field.
Runtime MCP exposure is deny-by-default. Schema v0.1 accepts the `schema`,
`record`, and `audit` resources plus the `search`, `create`, `update`, and
`delete` tools. Each capability must be named explicitly; `mcp: true` remains
a compatibility shorthand for read-only schema/record/search access. Domain
actions additionally require `actions.<name>.expose.mcp: true`. Exposure creates
protocol vocabulary but grants no permission, and every call is reauthorized
through the same application services as REST and local clients.

## Model facets

A field may contribute to several projections without mixing their concerns:

- storage: type, length, precision, nullability, indexes, and uniqueness;
- semantics: label, help, display format, and reference meaning;
- validation: local constraints and edit-mask contracts;
- presentation: preferred editor, width, alignment, input feedback, and view
  defaults;
- API: read/write representation and exposure policy;
- security: read and write permission requirements;
- auditing: omit, field-name-only change capture, or safe value capture;
- reporting: formatting and aggregation behavior.

The compiler combines these facets into one `FieldModel`. Adapters use the
facet appropriate to their job rather than inferring behavior independently.

## Relationships

Relationships may cross files and modules using qualified names. A reference
can declare both its storage column and inverse collection:

```yaml
# models/sales/invoice.yaml
entity: sales.Invoice

fields:
  id:           {type: integer, primary_key: true}
  number:       {type: string, length: 30, required: true, unique: true}
  invoice_date: {type: date, required: true}

  customer:
    type: reference
    target: crm.Customer
    storage: customer_id
    inverse: invoices
    required: true
    on_delete: restrict
    lookup_view: crm.Customer.lookup

  lines:
    type: collection
    target: sales.InvoiceLine
    inverse: invoice
    order_by: line_number
    cascade: [create, update]
    orphan_delete: true
```

`orphan_delete` also decides whether items may be removed at all. A collection
that declares it deletes the rows a commit leaves out; one that does not
rejects the removal, because the detached row would keep its foreign key and
reappear on the next read. Leaving the collection out of a payload entirely
still means "do not touch it".

The child declares the other side:

```yaml
# models/sales/invoice_line.yaml
entity: sales.InvoiceLine

fields:
  id:          {type: integer, primary_key: true}
  line_number: {type: integer, required: true}

  invoice:
    type: reference
    target: sales.Invoice
    storage: invoice_id
    inverse: lines
    required: true
    on_delete: cascade
```

The compiler normalizes a reference into foreign-key storage, object
navigation, lookup behavior, integrity validation, and adapter metadata.

Initial relationship goals are:

- many-to-one references;
- one-to-many collections;
- one-to-one relationships;
- self-references;
- explicit association entities.

Direct many-to-many syntax is deferred. Business associations frequently gain
attributes such as dates, roles, quantities, ordering, or status, making an
explicit association entity safer.

All model files are loaded before references are resolved. This two-pass model
allows circular relationships without Python-style import cycles.

## Display and lookup behavior

An entity has a stable display expression used by default in references:

```yaml
entity: crm.Customer
display: "{code} - {name}"
search_fields: [code, name, email]
```

A particular reference may override its lookup view or search policy. Lookup
queries remain subject to row and field permissions on the target entity.

References render as compact single-column selectors by default. A view may
request a searchable, multi-column lookup window for a particular reference:

```yaml
fields:
  product:
    editor: lookup
    lookup_view: catalog.Product.lookup
    allow_create: true
    create_view: catalog.Product.edit
```

The `lookup_view` may instead live on the reference field when every view uses
the same lookup. Lookup views declare ordinary secured columns and search
fields:

```yaml
view: catalog.Product.lookup
entity: catalog.Product
kind: lookup
columns: [code, name, unit_price]
search: [code, name]
```

`allow_create` is a presentation capability, not a permission grant. The
compiler requires `create_view` to resolve to a form for the referenced entity;
the runtime shows **New** only when the principal also has entity create access.
Nested creation commits the referenced record independently, then returns it to
the parent draft through the ordinary lookup-selection and `on_select` path.

A reference may declare which target rows are eligible to be chosen:

```yaml
product:
  type: reference
  target: catalog.Product
  lookup_view: catalog.Product.lookup
  lookup_filter: active == true
```

`lookup_filter` is one boolean expression over the **target** entity's fields
— the ordinary expression language, validated at compile time against the
target, with `$` parameters refused (the criterion is static; it cannot see
the caller or the draft). It is enforced in two places from the one
declaration. Every picker narrows by it: the terminal's select and lookup
editors, the browser's lookup dialog, and remote mode, all of which name the
reference edge on their queries and let the server resolve the rule from its
own model. And the commit refuses a **newly chosen** row the criterion
excludes, whichever surface or API call chose it, with an ordinary
field-scoped validation error.

What the filter never does is re-litigate history. A stored reference that
arrives unchanged in an edit is not re-checked — an invoice whose line names
a since-retired product stays readable and editable, and the stored value
still renders through the ordinary reference display. This is the same
stance TIDE takes toward rows it did not write. It is also the difference
from a row policy: a row policy hides rows from a principal everywhere,
while a lookup filter only narrows what may be *newly chosen* for one field,
leaving the target's own browse untouched.

Workflow invariants remain developer-owned entity metadata. For example:

```yaml
invoice_date:
  type: date
  immutable_when: "status != 'draft'"

status:
  type: choice
  readonly: true
  write: action_only
```

The compiler validates these expressions and every adapter consumes the same
normalized model. `RecordsService` enforces the rule again on commit, so a TUI,
later renderer, REST endpoint, or MCP tool cannot bypass a disabled editor.

A reference may copy secured target values into writable draft fields when a
record is selected:

```yaml
product:
  type: reference
  target: catalog.Product
  lookup_view: catalog.Product.lookup
  on_select:
    assign:
      description: {from: name, overwrite: always}
      unit_price: {from: unit_price, overwrite: always}
```

`overwrite` is either `always` or `when_blank`. Assignments are type-checked at
compile time and applied through `RecordsService`, including target-field read
and draft-field write authorization. Copied values remain stored snapshots; an
invoice line price does not change when the Product price changes later.

## Computed fields

Computed fields are part of the domain model and use the shared expression
system:

```yaml
total:
  type: decimal
  format: money
  readonly: true
  computed:
    expression: "quantity * unit_price"
    materialization: virtual
```

See [Expressions and validation](EXPRESSIONS-AND-VALIDATION.md) for computed
field modes, aggregates, filtering, and security inheritance.

## State transitions

An action that moves a record between states of a `choice` field declares that
move once, and the compiler derives the guards that follow from it:

```yaml
status:
  type: choice
  choices: [draft, posted, cancelled]
  default: draft
  readonly: true
  write: action_only

actions:
  post:
    label: Post
    permission: sales.invoice.post
    execute: actions.post_invoice
    enabled_when: "count(lines) > 0"
    transition:
      field: status
      from: draft
      to: posted
      locks_record: true
      stamp: {posted_at: now, posted_by: principal}
```

`from` accepts one state or a list of them. The compiler then:

- **derives the action's state guard.** `enabled_when` holds only the business
  half of the condition; the state half comes from `from`, and the resolved
  action carries `status == 'draft' and (count(lines) > 0)`. Writing the state
  half by hand is refused rather than checked for agreement, because two places
  that must agree is the problem being removed.
- **derives `immutable_when`** for every ordinarily writable field when some
  transition sets `locks_record: true`. Fields the workflow already owns —
  `action_only`, `system`, computed, read-only, and the primary key — are left
  alone. Omit `locks_record` and nothing is frozen.
- **checks the stamp targets** without writing them. The handler still records
  the change; the compiler verifies each named field exists, is
  `write: action_only`, and can hold `now` (a `datetime`) or `principal` (a
  `string`).
- **refuses a state nothing can reach.** Every declared choice must be the
  field's `default` or the `to` of some transition. `sales.Invoice` declared
  `cancelled` with no action producing it while the demo data seeded a record
  already in it — a row that could be neither posted nor edited, because
  neither was a draft.

The state field must be `write: action_only`, or an ordinary update could move
the record without running the action, and must declare a `default`, which is
the state a new record starts in.

An action may not be named `cancel` or `save`, whatever it does: those are the
form action bar's built-ins, so a domain action of that name never reaches a
form. Invoicing's is `void`, which is why the action and the `cancelled` state
it produces read differently.

This is deliberately a transition table on one field, not the general-purpose
workflow language the [decision log](DECISIONS.md) defers. There are no
parallel branches, no timers, no cross-entity effects; anything beyond a guard,
a lock and a stamp belongs in the action's Python handler.

## Action parameters

An action can declare the typed input it needs at the moment of execution,
with the same declaration reports use — the scalar `type`
(`string`/`integer`/`decimal`/`boolean`/`date`/`datetime`), `required`,
`default`:

```yaml
actions:
  void:
    label: Void
    permission: sales.invoice.void
    execute: actions.void_invoice
    parameters:
      reason: {type: string, required: true}
```

One block lands on all four surfaces: the REST invoke body becomes the
parameters object itself, the generated MCP tool grows a typed `parameters`
argument, and the Web and terminal renderers open a dialog. The dialog rule
is the `required` flag: an action with a required parameter asks before it
runs, while an action whose parameters are all optional stays one click and
keeps those parameters as a programmatic door — invoicing's `post` declares
an optional `occurred_at` so a migration can post as-of through the real
pipeline, and Post remains a single keystroke.

The `ActionService` owns the payload for every door. Values arrive as typed
values or their string forms (dialogs collect strings), defaults fill,
unknown names and missing required values are refused together under the
`action_parameter` rule, and an action declaring no parameters accepts only
an empty payload. Coercion happens before the idempotency fingerprint, so
the string form and the typed form of one request replay as one request.
The handler receives the typed mapping as its third argument; parameter
names must be plain identifiers (TIDE292), and `enabled_when` never sees
them — it guards the button, which exists before the input does.

An entity may declare `appearance:` rules — what a record means on sight,
before anyone opens it:

```yaml
appearance:
  - name: cancelled
    when: "status == 'cancelled'"
    emphasis: muted
  - name: nothing_to_post
    when: "status == 'draft' and total == 0"
    emphasis: warning
    fields: [total]
```

`when` is a boolean expression over the entity's own fields, checked by the
same compiler pass as an action's guards, so a rule keyed on a string is
refused rather than firing for every record that has one. `fields` names what
the rule speaks for; naming none means the record as a whole, which is the
grid row and the record card. Rules are ordered and the first match owns a
target, so precedence is read off the page rather than assigned as a number —
declaring two rules under one name is `TIDE280`.

`emphasis` is one of `info`, `success`, `warning`, `danger` and `muted`. It is
a name and never a colour: the framework renders it in a light theme, a dark
one and a terminal, and no hex value an author could write works in all three.
The author says what a record means; each renderer says what that looks like.

A rule may also carry `enabled: false` or `visible: false`:

```yaml
appearance:
  - name: priced_currency
    when: "total > 0"
    fields: [currency]
    enabled: false
  - name: unposted_stamps
    when: "status == 'draft'"
    fields: [posted_at, posted_by]
    visible: false
```

`enabled: false` locks what the rule speaks for — a field, or every ordinarily
writable field when the rule names none, the way a transition's `locks_record`
does. It is **enforced where `immutable_when` is enforced**: the service
refuses the write, so REST and MCP honour it and not just the browser, and the
locked fields leave `writable_fields` so no renderer offers the edit in the
first place. There is deliberately no second list on the wire saying the same
thing.

`visible: false` hides a field from a screen. It is presentation only and
**not a permission**: the value is still in the record the API returns, and a
principal who must not read it is stopped by a field permission instead. It
requires `fields:` — hiding a whole record is narrowing which records appear,
which is a named filter or a row policy, both of which paging and counts
already account for (`TIDE282`).

Both effects are subtractive. A rule may not *grant* either (`TIDE281`),
because granting would have to overrule the workflow lock or the permission
that withheld the thing.

Rules are evaluated server-side, per record, and travel as
`_tide.appearance` — absent when nothing matched, so an application that
declares no rules pays nothing. A condition that cannot be evaluated applies
nothing, which is the opposite of `immutable_when`: withholding an edit is
caution, while painting a record a colour that means something it is not is a
lie about the data.

## Documents

A record keeps a document in a field, so the schema says what each document
*is* rather than gathering them into a bucket beside the record:

```yaml
  signed_document:
    type: file
    label: Signed document
    help: The countersigned confirmation, attachable after posting.
    max_size: 10mb
    accept: [pdf, png, jpg]
    audit: values
```

An entity may declare as many as it has roles for — a purchase carrying a
`quotation`, a `supplier_invoice` and a `warranty` is three declared fields
with their own labels, layout placement, field security and audit. Where the
documents are genuinely unbounded rather than named, that is a collection of
child rows each holding a file, composed from the same primitive.

The field stores the attachment's key. The bytes live outside every database
on a filesystem the deployment names, and the metadata — filename, type,
size, digest, who uploaded it and when — is a framework table. Neither is the
application's to spell.

`max_size` is required and bounded by the framework's own ceiling of 100mb:
an author states a bound, inside a bound. `accept` narrows what a picker
offers and what an upload will take, as lowercase extensions. Everything that
decides about a *value* is refused, because the field does not hold one —
`unique`, `default`, `edit_mask`, `computed` and their relatives are
`TIDE289`. A file field is also not something a query may ask about: sorting,
filtering and summarizing all ask about the stored key rather than the
document, so none of them offer it.

Documents are a managed-database feature. A legacy schema is not TIDE's to
add a column or a table to, so a file field there is refused at compile time
(`TIDE290`) rather than at the first upload.

**A workflow lock freezes a file field only once it holds a file.** The
countersigned copy of a document arrives *after* the record it belongs to is
posted, which is the real order of events, so a locked record still accepts a
document it does not have yet and refuses to exchange or remove the one it
has. An author who wants a file field frozen outright writes `immutable_when`
and gets it verbatim.

## Schema evolution

Alembic executes migrations but does not decide model semantics. TIDE must
distinguish additions, renames, type changes, relationship changes, and
deletions explicitly.

The intended workflow is:

```text
tide model validate
tide db diff
tide db revision --name add-invoice-status \
  --proposal-fingerprint ... --database-fingerprint ... \
  --backup-evidence ...
tide db migrate
```

Migration proposals are reviewable. A renamed field must not be guessed as
"drop old column, create new column." Managed entities and persisted fields can
declare globally unique dotted `migration_id` values and bind an old physical
name with `renamed_from`. Table rename sources use
`{schema: optional_schema, table: old_name}`; field rename sources are old
column names. Rename intent is rejected in legacy mode.

The current `tide db diff` command implements the first read-only step. It
reflects the database, emits deterministic safety-classified changes and a
fingerprint, includes managed framework-state tables, recognizes only explicit
rename declarations, and performs neither rename inference nor DDL. An exact
fingerprinted proposal can now produce a non-overwriting, approval-bound
Alembic review revision and manifest. TIDE still provides no migration apply
path. See [Schema migrations](MIGRATIONS.md).

Schema evolution commands apply only to `database.mode: managed`. In legacy
mode, `tide db diff` becomes a read-only compatibility report and revision or
migration commands must refuse to run.

## Format independence

The compiler pipeline is:

```text
YAML/JSON -> parsed data -> typed source model -> merge and resolution
          -> normalized immutable ApplicationModel
```

JSON is the natural MCP/OpenAPI interchange representation even when developers
author YAML. A formatter may later provide:

```text
tide model format
tide model export --format json
```
