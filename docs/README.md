# TIDE Documentation

This directory is the living specification for TIDE. Documents should be
updated when a design decision changes; unresolved choices belong in the
decision log rather than being silently assumed in implementation code.

## Reading order

1. [Vision](VISION.md) explains the product and its boundaries.
2. [Architecture](ARCHITECTURE.md) defines the major runtime contracts.
3. [Application model](APPLICATION-MODEL.md) defines application organization,
   entities, relationships, and schema evolution.
4. [Legacy databases](LEGACY-DATABASES.md) defines physical mapping and the
   no-DDL contract for externally owned schemas.
5. [Compilation and application layout](COMPILATION-AND-LAYOUT.md) distinguishes
   metadata compilation from bytecode/native compilation and fixes the
   runtime/application directory boundary.
6. [Metadata contract v0.1](METADATA-V0.md) defines what the current compiler
   accepts and diagnoses.
7. [Presentation model](PRESENTATION.md) defines generated views, presets,
   overlays, formats, and renderer-specific settings.
8. [Expressions and validation](EXPRESSIONS-AND-VALIDATION.md) defines computed
   fields, criteria, filters, action conditions, and validation.
9. [Security](SECURITY.md) and the [threat model](THREAT-MODEL.md) define the
   permission model, protected values, boundaries, and baseline controls.
10. [REST API and MCP](API-AND-MCP.md) and [query and concurrency](QUERY-AND-CONCURRENCY.md)
   define machine-facing contracts.
11. [Designers and reporting](DESIGNERS-AND-REPORTING.md) describes TIDE Studio,
   view designers, and the banded report model.
12. [Terminal compatibility](TERMINAL-COMPATIBILITY.md) defines the initial
    terminal test matrix.
13. [Operational baseline](OPERATIONS.md) defines deployment, health, logging,
    backup, and recovery expectations.
14. [Headless runtime](HEADLESS-RUNTIME.md) documents the executable in-memory
    application-service contract.
15. [Roadmap](ROADMAP.md) orders the work into testable vertical slices.
16. [Decision log](DECISIONS.md) records accepted and unresolved decisions.

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
