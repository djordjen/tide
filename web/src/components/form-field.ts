/**
 * How one field occupies a form row.
 *
 * Stated once because two components render form fields — the editor and the
 * read-only detail sections — and both carried the same class literal, so the
 * rule could only ever be changed twice.
 *
 * There is no card. Every field used to be a bordered box with 12px of padding
 * above and below, spending 48px of packaging on a 36px control; a thirteen-
 * field invoice was then taller than a 900px screen, which put its lines
 * collection permanently below the fold. An input already looks like an input,
 * and a value that is not one looks like text — a plainer read-only signal
 * than a filled box, and one that costs no height. Errors are shown on the
 * control and in the message beneath it, so the cell needs no state of its own.
 */

const fieldLabelTextClass = "truncate text-xs font-medium text-muted-foreground"

/** The grid cell one field sits in. */
export const fieldCellClass = "min-w-0"

/**
 * Label and control as one unit, so the label can sit beside the control.
 *
 * A label above its control doubles the vertical space a record needs, and a
 * form is read down the value column: the terminal renderer and the desktop
 * application this replaces both put the label on the left. The rule itself
 * lives in `index.css` because it is a media query -- below 768px there is no
 * room for two columns and the label goes back above.
 */
export const fieldGroupClass = "tide-field"

/** A plain label for its control. */
export const fieldLabelClass = `mb-1 block ${fieldLabelTextClass}`

/** A label that shares its line with something else, such as a lock marker. */
export const fieldLabelRowClass = "mb-1 flex items-center justify-between gap-2"

export { fieldLabelTextClass }

/** A read-only value: text, never a box that invites a click. */
export const readOnlyValueClass = "min-h-6 text-sm"
