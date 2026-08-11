# Building with TIDE's web components

TIDE is a metadata-driven framework: its real application screens are generated
at runtime from compiled YAML, not composed by hand. What this design system
ships is the primitive layer those screens are built from, plus TIDE's theme.
Use it for the hand-written parts — shells, dialogs, empty states, settings.

## The one rule that will bite you

**The stylesheet is TIDE's own application build.** Tailwind v4 emits only the
utilities the source actually uses, so this design system's class vocabulary is
exactly "what TIDE's code uses" — nothing more. A class TIDE never writes is
not in the CSS, and an element using it silently renders with no styling at
all: `size-10` and `w-56` are both absent, and both collapsed a preview to
nothing during this import.

Before reaching for an arbitrary utility, check it exists in `styles.css` (and
the `_ds_bundle.css` it imports). When in doubt, prefer a class you can see one
of the components already using — read a component's source in the bundle.

## Wrapping and setup

No global provider is required. Two exceptions:

- **`Tooltip` needs `TooltipProvider`** as an ancestor, or the trigger throws.
- **Dark mode** is a `dark` class on an ancestor (usually `<html>`); every
  token below has a dark value already.

```jsx
<TooltipProvider>
  <Tooltip>
    <TooltipTrigger asChild><Button variant="outline">Post</Button></TooltipTrigger>
    <TooltipContent>A posted invoice can no longer be edited</TooltipContent>
  </Tooltip>
</TooltipProvider>
```

## The styling idiom

Tailwind utility classes over semantic tokens — never raw hex, never a literal
colour. The tokens are oklch CSS variables with light and dark values, surfaced
as these class families (all verified present in the shipped CSS):

| Purpose | Classes |
|---|---|
| Page and text | `bg-background`, `text-foreground` |
| Raised surfaces | `bg-card`, `bg-popover` |
| Emphasis | `bg-primary`, `text-primary-foreground` |
| Quiet actions | `bg-secondary`, `bg-accent` |
| De-emphasised | `bg-muted`, `text-muted-foreground` |
| Danger | `bg-destructive` |
| Lines and fields | `border-border`, `border-input` |
| App chrome | `bg-sidebar` and its `-foreground`/`-primary`/`-accent`/`-border` variants |
| Radius | `rounded-md`, `rounded-lg`, `rounded-xl` (from `--radius: 0.65rem`) |

Spacing and layout use ordinary Tailwind (`flex`, `grid`, `gap-2`, `gap-3`,
`space-y-3`), subject to the vocabulary rule above.

## Form fields

TIDE's forms are dense by decision: a field is a label and a control, with no
card, border, or padding wrapping the pair. A field spends at most 24px above
its control. Copy this shape rather than inventing one:

```jsx
<div className="min-w-0">
  <label className="mb-1 block truncate text-xs font-medium text-muted-foreground">
    Invoice Date
  </label>
  <Input type="date" defaultValue="2026-03-07" />
</div>
```

A writable field is an `Input`; a read-only one is plain text in the same
position — never a disabled input or a filled box.

## Where the truth lives

- `styles.css` and its `@import` closure — the authoritative token and utility
  set. Read it before styling.
- `components/<group>/<Name>/<Name>.prompt.md` — per-component API and usage.
- `Badge` carries record state in TIDE: `outline` for draft, `success` for
  posted, `warning` for cancelled. `success` and `warning` are TIDE's own
  additions to the base variant set.
