# TIDE Contacts Application

Contacts is TIDE Framework's compact second application. It deliberately uses
a different domain from Invoicing to prove that the compiler, secured services,
storage adapters, and renderers are framework capabilities rather than
invoice-specific code paths.

The baseline application was produced from the structured operations in
[`examples/ai_generation/contacts_plan.json`](../../examples/ai_generation/contacts_plan.json).
An automated test regenerates the candidate without writing and compares every
generated artifact with the checked-in YAML and Python handlers. The test also
crosses the real approval boundary in an isolated temporary workspace.

The generated baseline defines:

- Companies and Contacts, with a required Contact-to-Company reference;
- searchable browse, form, and lookup views;
- `contact_editor` and read-only `contact_viewer` roles;
- explicitly exposed REST and runtime-MCP CRUD operations;
- an idempotent **Archive contact** transition with server-owned audit stamps.

`demo_data.py`, `fake_data.py`, and this README are application-owned additions,
not generator output. There is intentionally no report in this small slice;
Invoicing remains the richer reporting and master-detail reference.

## Fastest test

On Windows:

```powershell
.\start.bat contacts-demo
```

Cross-platform:

```bash
uv run tide model validate applications/contacts
uv run --extra tui tide run applications/contacts --demo --role contact_editor
```

The isolated demo contains four Companies and seven Contacts. Select Companies
or Contacts from the application workspace, create and edit records, and archive
an active Contact. Closing the process discards the changes. To verify the
shared read-only security policy:

```powershell
.\start.bat contacts-viewer-demo
```

## Studio, Web, REST, and MCP

Open the same application metadata in Studio:

```powershell
.\start.bat contacts-studio
```

Run the browser renderer with framework-owned local username/password sign-in:

```powershell
.\start.bat contacts-web-demo
```

The first run creates a separate local Contacts identity store and securely
prompts for the `admin` password.

To inspect the generated OpenAPI document without starting a server:

```powershell
uv run tide api export-openapi applications/contacts
```

To expose the secured runtime-MCP resources and tools locally:

```powershell
.\start.bat contacts-mcp-demo
```

The server prints a development bearer token and mounts MCP at
`http://127.0.0.1:8000/mcp`. This runtime MCP changes Contacts data only through
the same services and permissions as REST; it is separate from developer MCP,
which proposes application source without data access.

## Persistent Faker data

For an explicitly initialized empty managed database, the application-owned
Faker profile accepts generic named counts:

```powershell
uv run --extra seed --extra sqlserver tide db seed applications/contacts `
  --database-env --role contact_editor `
  --count companies=25 --count contacts=100 --random-seed 20260806
```

The seeder creates Companies first, passes each Contact through normal reference
validation, and archives a deterministic subset through the real action service.
It refuses non-managed or non-empty databases.

## Generation proof

Previewing the source plan is always no-write:

```powershell
uv run tide app preview examples/ai_generation/contacts_plan.json --workspace .
```

Because `applications/contacts` already exists, `tide app apply` correctly
refuses to replace it. The repository test applies the same plan only inside a
fresh temporary workspace, verifies the evidence-bound approval contract, and
recompiles the result.
