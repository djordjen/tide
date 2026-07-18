# Metadata Contract v0.1

**Status: Accepted and executable.** This document defines the authoring rules
implemented by the initial compiler. It is intentionally smaller than the full
application-model vision.

## Version boundary

Every `tide.yaml` starts with a quoted source-schema version:

```yaml
schema_version: "0.1"

application:
  name: Example
  version: 0.1.0

database: {mode: managed}
```

`schema_version` controls metadata interpretation. `application.version`
versions the application built with that metadata. They are independent.

The compiler rejects unsupported schema versions, unknown properties, missing
required properties, invalid types, duplicate YAML keys, and mappings with
non-string keys. YAML merge keys are not supported; composition belongs in the
TIDE overlay model.

## Scalar behavior

TIDE uses strict boolean parsing. Only `true` and `false`, ignoring case, are
booleans. Values such as `yes`, `no`, `on`, and `off` remain strings. Authors
should quote values when a scalar could otherwise be ambiguous.

## Paths and discovery

Configured model, view, report, preset, and security paths are resolved relative to the
directory containing `tide.yaml`. They must stay inside that project root.
Directories are searched recursively for `.yaml` and `.yml` files.

Entity, view, report, and preset identifiers must be unique. Entity identifiers
and Python handler references use qualified dotted names.

## Current semantic validation

The v0.1 compiler checks:

- field types against the v0.1 set: string, integer, decimal, boolean, date,
  datetime, choice, reference, and collection;
- one primary key per entity and at most one integer concurrency token;
- relationship targets, inverse fields, and collection ordering fields;
- display, search, view, and report field references;
- semantic format and presentation-preset references;
- action handler shape, explicit action access, and shortcut conflicts;
- static project-handler module/function resolution without importing code;
- permission declarations, role grants, row policies, and field policies;
- the safe typed expression subset, relationship paths, parameters, expected
  result types, and computed field cycles;
- action-only/system fields being read-only to adapters;
- typed dynamic defaults (`default_factory: today` for date fields), mutually
  exclusive with literal `default` values;
- decimal precision/scale consistency and edit-mask compatibility: numeric
  picture masks must match their field type and declared scale, while regular
  expression masks are limited to string fields (`TIDE243`);
- collection views plus record-report access, primary-key parameter queries,
  typed expressions, root fields, detail collections/columns, and named formats
  (`TIDE251-256`);
- reference editor modes, lookup-view target compatibility, and type-safe
  `on_select` draft assignments;
- explicit inline-editor layouts: each row has at most two fields, every
  editable column appears exactly once, and computed, read-only, or hidden
  fields cannot be placed in the editor;
- lookup record creation declarations: `allow_create: true` requires a form
  `create_view` for the referenced TUI-exposed entity with declared create
  access (`TIDE242`);
- explicit physical table and persisted-column mappings for legacy databases.

`database.mode` is either `managed` (the default) or `legacy`. Legacy entities
must declare `storage.table`; `storage.schema` is optional. Every persisted
scalar field must declare `column`, and every persisted reference must declare
its existing foreign-key column through `storage`. Missing mappings are
`TIDE228` and `TIDE229` errors. Collections and virtual computed fields are not
persisted and therefore do not require column mappings.

Numeric `precision` and `scale` are model constraints, not display hints.
Record services reject values that exceed either limit. `edit_mask` may add a
renderer-neutral input contract: numeric fields accept a picture such as
`"0.00"`, while string fields accept `{regex: "..."}`. Adapters may provide
earlier feedback, but service validation remains authoritative.

JSON Schema can be exported for each source-file kind:

```bash
tide model schema project
tide model schema entity --output entity.schema.json
```

## Overlay behavior

Views resolve through framework defaults, application defaults, named presets,
entity presentation settings, optional base views, and the specific overlay.
Scalars replace, mappings merge recursively by key, and lists replace. `null`
is a literal value, not an implicit remove operation. Explicit removal remains
a future dedicated operation.

For an `inline_edit` view, `columns` defines the collection table and
`layout[*].rows` optionally defines the separate detail editor. Each layout row
contains one or two field names. The first positions form the left editor
column, the second positions form the right, and keyboard traversal follows
the complete left column before the right. Omitting `layout` retains the
generated editable-column order. Invalid explicit layouts report `TIDE241`.

Form layout sections may declare an optional non-empty, single-line `tab`
label. Repeated labels place sections on the same tab; unlabelled sections use
`General` when at least one tab is present. Form views may declare an ordered
`actions` sequence containing `cancel`, `save`, and action names defined by the
entity. Collection sections may declare an ordered subset of `add`, `apply`,
and `remove`. Omitted sequences use renderer defaults. Collection `view` values
must resolve to an `inline_edit` view for the collection target entity. Invalid
tab, action-bar, or inline-view presentation reports `TIDE244`.

`tide view explain` returns the resolved view plus provenance for every leaf or
replaced collection, including the layer, source file, and source property path.

## Diagnostics

Human diagnostics use:

```text
file:line:column: error [TIDE205] unknown relationship target 'missing.Person'
```

Machine output is available through `tide model validate --json`. Codes and
source locations are compatibility-sensitive. The current ranges are:

| Range | Meaning |
|---|---|
| `TIDE001-012` | file, YAML, and project discovery |
| `TIDE100-103` | typed source-schema validation |
| `TIDE200-269` | model, view, preset, report, handler, and security resolution |
| `TIDE300-308` | expression parsing, safety, and typing |

Diagnostics carry a severity. Errors fail compilation; warnings do not. A
successful `tide model validate` prints warnings and includes them in the
`--json` output under `warnings`. `TIDE226` is an error: every action must
declare a `permission` or explicitly opt into `unrestricted: true`. Declaring
both forms is rejected with `TIDE227`.

Reports have the same fail-closed access rule as actions: each report declares
`permission` or explicitly sets `unrestricted: true`. The executable v0.1
report kind binds one required parameter to its entity primary key and permits
one collection detail band. Report REST delivery is separately deny-by-default
and requires `expose: {rest: true}`. Richer set-based/grouped queries remain
outside this contract until they have a bounded SQL-translatable query plan.

Entity MCP exposure is also typed rather than an open capability list. The
implemented v0.1 values are `resources: [schema, record]` and
`tools: [search]`. Unknown resource/tool names fail source-schema validation;
action/report MCP flags remain reserved for later adapters and do not register
write capabilities today.
