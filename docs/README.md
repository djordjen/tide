# TIDE Documentation

This directory is the living specification for TIDE. Documents should be
updated when a design decision changes; unresolved choices belong in the
decision log rather than being silently assumed in implementation code.

## Reading order

1. [Getting started](GETTING-STARTED.md) takes a new user from a clean checkout
   through the demo TUI, Studio, REST/OpenAPI, MCP, and optional SQL Server.
2. [Build your first TIDE application](FIRST-APPLICATION.md) creates an
   independent, runnable Contacts application from compiler-validated YAML.
3. [Invoicing application walkthrough](INVOICING-WALKTHROUGH.md) connects the
   current TUI workflow and screenshots to the exact YAML and Python sources.
4. [REST API client tutorial](API-CLIENT-TUTORIAL.md) runs an authenticated
   Invoice workflow through FastAPI, including ETags, idempotency, audit, and
   reporting.
5. [AI-assisted generation tutorial](AI-GENERATION-TUTORIAL.md) connects local
   ChatGPT/Codex to developer MCP and carries an exact candidate through the
   separate explicit local approval boundary.
6. [Documentation plan](DOCUMENTATION-PLAN.md) records the small, testable
   tutorial tranches planned next.
7. [Vision](VISION.md) explains the product and its boundaries.
8. [Architecture](ARCHITECTURE.md) defines the major runtime contracts.
9. [Application model](APPLICATION-MODEL.md) defines application organization,
   entities, relationships, and schema evolution.
10. [Schema migrations](MIGRATIONS.md) defines deterministic database diff
   proposals, safety classifications, rename refusal, and the future
   approval-bound Alembic workflow.
11. [Legacy databases](LEGACY-DATABASES.md) defines physical mapping and the
    no-DDL contract for externally owned schemas.
12. [Microsoft SQL Server](SQL-SERVER.md) defines the first multi-user dialect,
    driver, connection, and test contract.
13. [Windows quick start](WINDOWS-QUICKSTART.md) documents the repository
    shortcut, local SQL Server initialization, and normal/demo launch modes.
14. [Compilation and application layout](COMPILATION-AND-LAYOUT.md)
    distinguishes metadata compilation from bytecode/native compilation and
    fixes the runtime/application directory boundary.
15. [Metadata contract v0.1](METADATA-V0.md) defines what the current compiler
    accepts and diagnoses.
16. [Presentation model](PRESENTATION.md) defines generated views, presets,
    overlays, formats, and renderer-specific settings.
17. [Expressions and validation](EXPRESSIONS-AND-VALIDATION.md) defines computed
    fields, criteria, filters, action conditions, and validation.
18. [Security](SECURITY.md) and the [threat model](THREAT-MODEL.md) define the
    permission model, protected values, boundaries, and baseline controls.
19. [REST API and MCP](API-AND-MCP.md),
    [query and concurrency](QUERY-AND-CONCURRENCY.md), and
    [shared cursor storage](CURSOR-STORAGE.md) define machine-facing query and
    continuation contracts.
20. [Action audit and idempotency](AUDIT-AND-IDEMPOTENCY.md) defines durable
    reservations, audit rows, replay, and crash reconciliation.
21. [AI-assisted application generation](AI-APPLICATION-GENERATION.md) defines
    developer MCP proposals, isolated runtime-checked candidates, approval, and
    source-write boundaries.
22. [Designers and reporting](DESIGNERS-AND-REPORTING.md) describes TIDE Studio,
    view designers, and the banded report model.
23. [Terminal compatibility](TERMINAL-COMPATIBILITY.md) defines the initial
    terminal test matrix.
24. [Operational baseline](OPERATIONS.md) defines deployment, health, logging,
    backup, and recovery expectations.
25. [Headless runtime](HEADLESS-RUNTIME.md) documents the executable in-memory
    application-service contract.
26. [Roadmap](ROADMAP.md) orders the work into testable vertical slices.
27. [Decision log](DECISIONS.md) records accepted and unresolved decisions.

## Status vocabulary

- **Accepted**: the current design direction; changes should update the
  relevant documents and decision log.
- **Proposed**: expected direction that still needs implementation experience.
- **Deferred**: deliberately outside the initial milestones.
- **Open**: a choice that has not been made.

## Documentation rules

- Examples describe the normalized intent, even before a parser exists.
- Metadata syntax should remain concise, explicit, and safe to validate.
- Security requirements are contracts, not presentation suggestions.
- Complex behavior belongs in Python rather than expanding YAML into a general
  programming language.
- New adapters must consume application services and may not reach around them
  to access SQLAlchemy directly.
