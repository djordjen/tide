# Build Your First TIDE Application

This tutorial follows the checked-in
[Contacts application](../applications/contacts/README.md), TIDE Framework's
compact second application. It demonstrates Companies, Contacts, a reference
lookup, two roles, one workflow action, demo/Faker data, and the same compiled
presentation contract in TUI, Qt, and Web.

Unlike the richer Invoicing reference, Contacts starts from a structured
generation plan. The plan is checked in at
[`examples/ai_generation/contacts_plan.json`](../examples/ai_generation/contacts_plan.json),
and CI proves that its deterministic generated artifacts exactly match the
maintained application.

## 1. Preview the structured plan

From the repository root:

```powershell
uv run tide app preview examples/ai_generation/contacts_plan.json --workspace .
```

Preview validates typed operations, creates a temporary candidate, compiles it,
runs bounded security/CRUD/action checks, returns the exact diff, and deletes
the candidate. It does not write to `applications/`, run an external command,
or connect to an application database.

In this checkout the command reports `ready: false` only because the reviewed
`applications/contacts` destination already exists. The candidate's checks and
12 artifacts still complete; replacement remains correctly prohibited.

The checked-in Contacts plan produces 12 baseline artifacts:

```text
applications/contacts/
  tide.yaml
  actions.py
  runtime.py
  models/crm/{company,contact}.yaml
  views/crm/{company,contact}-{browse,edit,lookup}.yaml
  security/policies.yaml
```

`demo_data.py`, `fake_data.py`, and `README.md` are deliberate application-owned
additions after generation.

## 2. Understand the model

[`company.yaml`](../applications/contacts/models/crm/company.yaml) defines a
unique code, name, optional website, and active flag.

[`contact.yaml`](../applications/contacts/models/crm/contact.yaml) defines a
required reference to Company plus an action-owned status and archive stamps.
The reference is normalized against Company's integer primary key, and
`on_delete: restrict` prevents a referenced Company from being removed.

Both entities explicitly opt operations into REST and MCP. Their permissions
are also explicit; omitting a mutation permission never makes it unrestricted.

## 3. Understand shared presentation

The six documents under [`views/crm`](../applications/contacts/views/crm/)
define browse columns, search fields, lookup columns, and two-column form rows.
TUI, Qt, and Web interpret that same semantic ordering. Renderer-specific
measurements do not rewrite the application YAML.

Contact is marked as the default browse, while the compiler's useful fallback
navigation exposes both Companies and Contacts under one Application group.

## 4. Understand security and workflow

[`security/policies.yaml`](../applications/contacts/security/policies.yaml)
defines:

- `contact_editor`, which may maintain both entities and archive Contacts;
- `contact_viewer`, which may only read them.

The generated `archive` action is visible only with `crm.contact.archive`, is
enabled only for active Contacts, and updates status, time, and principal
through the server-side action service. Its idempotency declaration applies
equally to TUI, REST, and runtime MCP.

## 5. Validate and run it

```powershell
uv run tide model validate applications/contacts
uv run --extra tui tide run applications/contacts --demo --role contact_editor
```

Expected validation output:

```text
Model is valid: TIDE Contacts 0.1.0 (2 entities, 6 views, 0 reports, 0 warning(s)).
```

On Windows the equivalent shortcut is:

```powershell
.\start.bat contacts-demo
```

Try creating a Company, creating a Contact through the Company lookup, and
archiving that Contact. Then restart with `contacts-viewer-demo` and confirm
that mutation actions are absent or disabled.

## 6. Run other renderers and interfaces

The application README contains the complete commands. The shortest Windows
paths are:

```powershell
.\start.bat contacts-studio
.\start.bat contacts-web-demo
.\start.bat contacts-api-demo
.\start.bat contacts-gui
.\start.bat contacts-mcp-demo
```

Qt is an API client, so keep `contacts-api-demo` running while starting
`contacts-gui`. Web starts its API automatically and uses a separate TIDE-owned
username/password store. OpenAPI documents the generated REST contract but
does not authorize a caller or bypass service security.

## 7. Generate another application

Copy the Contacts plan to a new filename, change `application_id`, application
name, entities, fields, roles, and actions, then preview it. For a new absent
destination only, `tide app apply` displays the candidate again and requires
the complete evidence-bound approval phrase before publishing it.

See [AI-assisted generation](AI-GENERATION-TUTORIAL.md) for ChatGPT/Codex plus
developer MCP, and use the maintained
[Invoicing application](../applications/invoicing/README.md) when you need
master-detail collections, computed decimals, reports, concurrency, auditing,
and more advanced layouts.
