# design-sync notes — TIDE

## What this repo is, for a sync

TIDE is a Python framework whose web renderer (`web/`) is an **application**,
not a component library. `@tide-framework/web` is `private: true` with no
`main`/`module`/`exports`, and `npm run build` emits an app bundle. The synced
surface is deliberately scoped to `web/src/components/ui/` — 7 files, 24 named
exports — plus TIDE's theme. The 11 application components (`record-detail`,
`browse-workspace`, `tide-data-grid`, …) take a `TideApi`, a presentation
manifest and a TanStack Query context, so they cannot render in a design tool
and are out of scope by design, not by omission.

## Setup that is NOT in config, and is needed per clone

- **Self-resolution junction.** The converter resolves the package through
  `node_modules/<pkg>`, which a repo's own package never has. Create it once
  per clone (Windows):
  `cmd /c mklink /J web\node_modules\@tide-framework\web <abs path to web>`
  (POSIX: `ln -s ../../. web/node_modules/@tide-framework/web`).
  Do **not** solve this with `--entry`: passing one makes the converter treat
  that file as a built dist entry, which skips the synth-from-`src/` path and
  fails with `[ZERO_MATCH]`.
- **Run the converter with no `--entry`**, from the repo root:
  `node .ds-sync/package-build.mjs --config .design-sync/config.json --node-modules web/node_modules --out ./ds-bundle`
- **playwright** must be installed in `.ds-sync/` at a version whose
  `browsers.json` pins the cached chromium build. Today: cache has
  `chromium-1234`, the repo pins `@playwright/test@1.62.0` which matches, so
  `npm i playwright@1.62.0` inside `.ds-sync/` reuses the browser.

## The stylesheet, and why `buildCmd` has a `cp` in it

`cssEntry` points at `dist/ds-styles.css`, which is a **copy** of the
content-hashed `dist/assets/index-<hash>.css` vite emits. The hash changes on
every build, so `buildCmd` copies it to a stable name; `tokensGlob` is not an
option here because it resolves relative to `--node-modules`, not the package.

**The important consequence:** the shipped CSS is TIDE's *application* build,
and Tailwind v4 only emits utilities the source uses. The design system's class
vocabulary is therefore exactly "what TIDE's own code uses". `size-10` and
`w-56` are absent and silently collapsed two preview elements during this
import. This is documented for the design agent in `conventions.md`; when
authoring previews, verify a class exists:
`grep -c "\.<class>[^a-z0-9-]" ds-bundle/_ds_bundle.css`.

## Known render warns

None. The final validate run is clean — no `[RENDER_*]`, no `[GRID_OVERFLOW]`,
no `[FONT_MISSING]`. A warn on a future run is new; look at it.

## Fixes applied to TIDE itself during this sync

- `web/src/index.css` declared `font-family: Inter, …` and nothing ever loaded
  Inter — no `@font-face`, no font file, no font host. The UI rendered in Inter
  only for someone who happened to have it installed. Inter was dropped from
  the stack so the declaration names what actually renders. If TIDE ever wants
  the Inter look, ship the woff2 + an `@font-face` and wire `cfg.extraFonts`.

## Overrides, and why

- `DropdownMenu` and `Tooltip` are `cardMode: "single"` — their content
  portals, so multi-cell grids cannot present them. Each names a
  `primaryStory`.
- `DropdownMenuLabel` has its own authored preview because it is a styled
  `div`: the floor card passed it no children and screenshotted blank.

## Re-sync risks

- **The `cp` in `buildCmd` is load-bearing.** Skip it and `cssEntry` points at
  a stale copy — previews render against last build's CSS with no warning.
- **The junction is per-clone and gitignored.** A fresh clone fails at
  `dts.mjs projectFor` with ENOENT until it is recreated.
- **Class vocabulary tracks TIDE's source.** If TIDE stops using a utility a
  preview or `conventions.md` names, that class silently leaves the CSS. The
  conventions table was verified against `_ds_bundle.css` at sync time — re-run
  that verification on every sync (`ring-ring` was already dropped from the
  table because TIDE only uses it with opacity modifiers).
- **16 of 24 components are on the floor card by choice** — the DropdownMenu
  and Tooltip sub-parts, which are best seen inside their parent's composition.
  They are fully importable and documented; author previews for them on any
  later sync if the picker feels thin.
- The 24-export count comes from a content scan of `src/components/ui/`, not a
  `.d.ts` tree. Adding a file there changes the count with no other signal.
