# TIDE Applications

Each direct child of this directory is a self-contained TIDE application with
its own `tide.yaml`. Application metadata and Python handlers belong here;
reusable framework/runtime code belongs under `src/tide/`.

The framework wheel deliberately excludes this directory. An application can
therefore be versioned and deployed beside this checkout or packaged
independently with `tide-framework` as a dependency.

Validate the included application from the repository root:

```bash
uv run tide model validate applications/invoicing
```
