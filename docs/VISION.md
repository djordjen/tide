# TIDE Vision

TIDE brings the speed and coherence of classic database RAD development to
modern business applications, with the terminal as a first-class user
interface rather than a nostalgic skin.

## Product identity

TIDE is primarily a business-application runtime. Textual is its first
presentation adapter; REST, MCP, reports, and a future web renderer are peers
that use the same domain behavior.

```text
Application model + Python behavior
                 |
                 v
       Secured application runtime
                 |
       +---------+---------+---------+
       |         |         |         |
       v         v         v         v
      TUI       REST      MCP      Reports
                                      |
                                  Future web
```

## What TIDE learns from earlier systems

| Influence | Preserve | Avoid |
|---|---|---|
| Clarion | Central dictionary, integrated browses/forms/reports, formatting pictures, templates, keyboard productivity, extension points | Editable generated code and regeneration conflicts |
| web2py | A field definition informing storage, validation, representation, and controls; immediate runnable applications; batteries included | Global magic, implicit behavior, and unsafe automatic migrations |
| XAF | Application Model, Object Space/unit of work, Actions, validation, security, modules, and layered model differences | Deep inheritance and a large abstraction tower |
| TIDE | Terminal and SSH operation, mouse support, open metadata, AI-ready inspection | Prematurely becoming a generic no-code platform |

## User experience

A TIDE application should support:

- complete keyboard operation and configurable shortcuts;
- mouse clicks, double-clicks, wheel scrolling, and appropriate drag actions;
- browses with sorting, filtering, paging, incremental search, and named
  filters;
- forms with parsing, formatting, validation, references, and lookups;
- transactional master-detail editing;
- menus, actions, dialogs, tabs, notifications, and role-aware behavior;
- printable reports and controlled exports;
- local SQLite operation and multi-user PostgreSQL deployment;
- local terminal and remote SSH sessions.

## Developer experience

A developer should be able to:

1. define entities and relationships in compact YAML files;
2. run useful generated views immediately;
3. inspect the fully resolved model and understand every inherited setting;
4. evolve the database through explicit, reviewable migrations;
5. add custom business rules and actions in ordinary Python;
6. expose selected capabilities through REST and MCP;
7. test rules without launching a user interface;
8. package and deploy the application predictably;
9. use an AI agent through a safe developer MCP server;
10. customize generated views later through TIDE Studio.

## Scope boundaries

TIDE is not:

- a terminal skin over a web application;
- a replacement for SQLAlchemy, PostgreSQL, FastAPI, or Textual;
- a new general-purpose programming language;
- purely a visual no-code builder;
- a framework that hides the underlying Python application;
- an excuse for REST, MCP, reports, or designers to bypass application
  security.

The first useful release should solve one complete invoicing vertical slice
rather than offering many shallow designers and adapters.
