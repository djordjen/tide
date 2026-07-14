# Compilation and Application Layout

## Two meanings of compilation

web2py can bytecode-compile an application's models, controllers, and views.
That is a production/distribution operation: Python and generated template code
are compiled so the application can be shipped in compiled form.

TIDE's current **model compiler** performs a different job. It:

1. strictly parses application YAML;
2. validates types, references, permissions, handlers, and expressions;
3. applies deterministic defaults, presets, and overlays;
4. produces an immutable normalized `ApplicationModel` for every adapter.

It does not create a standalone executable, compile Python handlers to
bytecode, or hide application source. TIDE still benefits from ordinary Python
bytecode caching automatically, but that is separate from the model contract.

For production, metadata is validated in CI and compiled again at startup so
an invalid application fails before serving work. A future normalized-model
cache may reduce startup time, but it is disposable, fingerprinted against all
inputs, and never becomes the editable source of truth.

## Runtime/application boundary

When applications and the framework share a checkout, the conventional layout
is:

```text
src/tide/                  framework runtime
applications/
    <application-name>/    application source root
        tide.yaml
        models/
        views/
        presentation/
        reports/
        security/
        actions.py
tests/                     framework tests
```

The directory containing an application's `tide.yaml` is its confinement root:
manifest discovery cannot escape it. `applications/` itself is not bundled in
the TIDE framework wheel, which keeps application ownership and licensing
separate from runtime distribution.
