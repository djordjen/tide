# Security Model

## Unified enforcement

TUI, web, REST, MCP, reports, imports, exports, and background operations use
the same authorization services. Presentation adapters may hide unavailable
commands for usability, but security is enforced again in application
services.

An action permission is independent from the entity update permission. A
principal may be allowed to post an invoice through the reviewed domain action
without receiving unrestricted invoice editing rights. The action service still
requires record read access, evaluates row policy, rechecks its precondition,
and applies field write policies during commit.

## Permission dimensions

TIDE distinguishes:

- entity operations: list, read, create, update, and delete;
- row access: which records a principal may query or modify;
- field access: read and write independently;
- action execution;
- navigation and view access;
- report execution;
- import and export;
- administrative and model-development capabilities.

Roles grant permission identifiers or policies; model fields should reference
permission identifiers rather than embedding particular role names.

## Row security

Row policies become part of the database query. Unauthorized records must not
be loaded and then filtered by a TUI widget or serializer. Query services apply
row policies to lists, lookups, reports, relationships, aggregates, REST, and
MCP consistently.

## Protected fields

When a user may see a record but not a field, TIDE uses an internal sentinel:

```python
ProtectedValue
```

It is not the string `"Protected Content"`. A TUI or web form may render a
localized placeholder:

```text
Salary: [ Protected Content ]
```

The sentinel ensures that:

- a protected number or date does not become a string;
- the placeholder cannot be saved to the database;
- change tracking does not mark the field dirty;
- different adapters can serialize protection appropriately;
- localized renderers can choose their own display text.

## Stable API shape

REST and MCP should preserve a predictable structure without returning a fake
typed value. One proposed REST representation is:

```json
{
  "id": 42,
  "first_name": "John",
  "last_name": "Smith",
  "salary": null,
  "_tide": {
    "protected_fields": ["salary"]
  }
}
```

The metadata distinguishes a protected field from a genuine database null.
The exact versioned wire contract remains an open decision.

## Read and write independence

A field may have separate read and write permissions. If blind writes are
allowed, PATCH semantics and `RecordSession` change tracking must ensure that
an unchanged protected field is never overwritten. Full-object updates may not
interpret missing, null, or protected values as equivalent.

## Inference prevention

A principal without read access to a field normally cannot:

- filter or sort by it;
- group, total, or aggregate it;
- export or report it;
- receive validation errors containing its value;
- infer it through computed fields;
- query it through MCP;
- use it as an unrestricted lookup display value.

Computed fields inherit restrictive dependency permissions by default. Any
exception must be explicit and testable.

## Exposure versus authorization

Metadata such as `expose.rest` or `expose.mcp` determines whether an interface
exists. It does not grant permission to any user. Both exposure configuration
and runtime authorization must permit an operation.

Exposure is deny-by-default for machine interfaces, and mutation operations
are enabled explicitly.

## Authentication adapters

TIDE's core consumes a `Principal`; authentication belongs to adapters:

- local or SSH-backed login for TUI deployments;
- bearer/OAuth-compatible identity for REST and HTTP MCP;
- service principals for background work;
- delegated user identity for AI agents.

All identities map into the same permission and audit model.

Schema v0.1 is single-tenant per deployment. Multi-user does not imply
multi-tenant: tenant identifiers must not be added as an informal row filter.
Multi-tenant support requires an explicit isolation and migration contract.

## Auditing

Audit records should include principal, channel, action, entity, identity,
timestamp, correlation identifier, outcome, and permitted change details.
Protected values must not leak into logs. MCP and export operations deserve
explicit audit events because of their potential breadth.
