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
- action handler shape and shortcut conflicts;
- static project-handler module/function resolution without importing code;
- permission declarations, role grants, row policies, and field policies;
- the safe typed expression subset, relationship paths, parameters, expected
  result types, and computed field cycles;
- action-only/system fields being read-only to adapters;
- collection views and report entity references.

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
`--json` output under `warnings`. The first warning is `TIDE226`: an action
that declares no `permission` is executable by any principal who can read the
entity.
