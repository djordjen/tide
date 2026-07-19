# Build Your First TIDE Application

This tutorial builds a small Contacts application without changing the TIDE
runtime. It demonstrates the smallest useful vertical slice: one entity, two
views, explicit permissions, TUI access, and generated REST/OpenAPI and MCP
contracts.

The finished, CI-validated files are in
[`docs/examples/first-application`](examples/first-application/). Copy that
directory to `applications/contacts` if you want to run it unchanged:

```powershell
Copy-Item docs/examples/first-application applications/contacts -Recurse
```

## 1. Create the application manifest

Create `applications/contacts/tide.yaml`:

```yaml
schema_version: "0.1"

application:
  name: TIDE Contacts
  version: 0.1.0

model:
  paths: [models]

views:
  paths: [views]

security:
  paths: [security]
```

The application owns its YAML below `applications/contacts`; the reusable
framework remains below `src/tide`. TIDE compiles all referenced documents into
one normalized model before any renderer or data adapter uses them.

## 2. Define a Contact

Create `applications/contacts/models/contact.yaml` using the
[validated example](examples/first-application/models/contact.yaml). Its main
sections have distinct responsibilities:

- `fields` defines the logical data model;
- `expose` opts the entity into TUI, REST, and MCP surfaces;
- `permissions` gives every operation an explicit security requirement;
- `display` and `search_fields` provide shared presentation defaults.

The `email` validation, required `name`, string lengths, and permissions are
enforced below the renderer, so TUI, REST, and MCP cannot disagree about them.

## 3. Define browse and edit views

Copy the validated
[browse view](examples/first-application/views/contact-browse.yaml) and
[edit view](examples/first-application/views/contact-edit.yaml) into the new
application's `views` directory.

The browse document chooses columns and search fields. The edit document uses
rows to describe placement, while each renderer remains responsible for its
native controls and responsive behavior. Layout metadata does not redefine the
entity or its validation.

## 4. Grant application roles

Copy the validated
[security policy](examples/first-application/security/policies.yaml) into
`applications/contacts/security/policies.yaml`.

`contact_manager` may read and modify records. `contact_viewer` is read-only.
These are application roles used by the shared security engine; hiding a button
in a client is never the authorization boundary.

## 5. Validate before running

```powershell
uv run tide model validate applications/contacts
```

Expected output:

```text
Model is valid: TIDE Contacts 0.1.0 (1 entities, 2 views, 0 reports, 0 warning(s)).
```

Validation checks references, field types, view members, permissions, and the
other metadata contracts without connecting to a database.

## 6. Run the TUI

```powershell
uv run --extra tui tide run applications/contacts --demo --role contact_manager
```

The demo repository is in memory. Add, edit, search, and delete Contacts; all
changes disappear when the process exits. To verify the read-only role, start a
second session with `--role contact_viewer`.

## 7. Inspect the generated API

Export OpenAPI without starting a server:

```powershell
uv run tide api export-openapi applications/contacts
```

The Contact routes exist because `expose.rest` opted them in. OpenAPI describes
the contract; it does not bypass authentication or any Contact permission.

Runtime MCP follows the same rule: only the resources and tools named in
`expose.mcp` are generated, and their calls still pass through application
services and security.

## What to change next

Try one small change at a time and validate after each one:

1. add a `phone` string field and place it on both views;
2. add a `company` entity and a Contact-to-Company reference;
3. add a lookup view for Company selection;
4. define a row policy if viewers should see only a subset of Contacts;
5. add a report only after the model and workflow are useful.

Use [Metadata contract v0.1](METADATA-V0.md) for syntax and the maintained
[Invoicing application](../applications/invoicing/README.md) for relationships,
line items, actions, reports, auditing, and more advanced presentation.
