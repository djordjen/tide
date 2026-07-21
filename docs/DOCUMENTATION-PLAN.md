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
- [REST API client tutorial](API-CLIENT-TUTORIAL.md): an executable authenticated
  create/update/Post/report workflow with validation, ETags, idempotency, and
  correlated audit history;
- [AI-assisted generation tutorial](AI-GENERATION-TUTORIAL.md): connect local
  ChatGPT/Codex to developer MCP, propose and preview a structured application,
  then cross the separate explicit local approval boundary;
- [Qt GUI prototype](QT-GUI.md): install and run the first native read-only
  renderer through the secured remote API boundary;
- [Windows quick start](WINDOWS-QUICKSTART.md): `start.bat` modes and local
  setup;
- focused reference and architecture documents listed in the
  [documentation index](README.md).

## Next documentation work

No additional standalone documentation tranche should get ahead of the
implementation. The next guide will accompany the next user-visible feature;
production deployment, expanded Qt editing, web rendering, and controlled
migration execution remain deliberately deferred below.

Each tranche should include runnable commands, expected output, relevant
screenshots or a small diagram, and CI checks for local links and compilable
examples where practical.

## Later, when the feature is real

- production deployment and packaging guide;
- expanded Qt GUI editing guide and renderer comparison;
- web renderer guide;
- controlled migration execution and recovery tutorial;
- reusable module/plugin authoring.

These topics remain short architecture notes until their workflows can be
demonstrated end to end. Documentation must not imply that a planned capability
is already available.
