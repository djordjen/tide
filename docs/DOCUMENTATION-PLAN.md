# Documentation Plan

Status: **active, deliberately incremental**.

TIDE documentation is delivered with working features and kept as a living
part of the repository. The immediate goal is not to document every possible
future feature; it is to make the currently runnable framework understandable
and testable by a new developer.

## Available now

- [Getting Started](GETTING-STARTED.md): five-minute demo, Invoicing tour,
  Studio, REST/OpenAPI, MCP, SQL Server, and project checks;
- [Build Your First TIDE Application](FIRST-APPLICATION.md): an independent
  Contacts application backed by checked-in, compiler-validated YAML;
- [Invoicing Application Walkthrough](INVOICING-WALKTHROUGH.md): an illustrated
  task-oriented tour tied to the exact model, view, security, action, and report
  sources;
- [Windows quick start](WINDOWS-QUICKSTART.md): `start.bat` modes and local
  setup;
- focused reference and architecture documents listed in the
  [documentation index](README.md).

## Next documentation tranche

1. **API client tutorial** — authenticate, list/create/update with ETags, invoke
   an action idempotently, inspect the audit correlation, and relate each call
   to OpenAPI.
2. **AI-assisted generation tutorial** — use developer MCP to propose and
   preview an application, then use the separate local approval command. This
   should follow a stable, reproducible MCP client setup.

Each tranche should include runnable commands, expected output, relevant
screenshots or a small diagram, and CI checks for local links and compilable
examples where practical.

## Later, when the feature is real

- production deployment and packaging guide;
- Qt GUI guide and renderer comparison;
- web renderer guide;
- controlled migration execution and recovery tutorial;
- reusable module/plugin authoring.

These topics remain short architecture notes until their workflows can be
demonstrated end to end. Documentation must not imply that a planned capability
is already available.
