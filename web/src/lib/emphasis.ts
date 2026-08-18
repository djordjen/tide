import type { TideRecord } from "@/lib/contracts"

/**
 * What an application's `appearance:` rules asked for, and what that looks
 * like here.
 *
 * The renderer never sees a rule. The server evaluates them against the record
 * and sends a verdict -- `_tide.appearance` -- so the expression engine stays
 * where the data is and every surface paints the same records.
 *
 * The author says what a record means; this file says what that looks like in
 * two themes. That split is why the wire carries `warning` and not `#FFFF88`:
 * a colour authored once renders wrong in one of the themes and has nothing to
 * say to a terminal at all.
 */

export type TideEmphasis =
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "muted"

const EMPHASIS_VALUES: readonly string[] = [
  "info",
  "success",
  "warning",
  "danger",
  "muted",
]

/** A row in a list: a left edge and a wash, quiet enough to scan past. */
const ROW_CLASSES: Record<TideEmphasis, string> = {
  info: "border-l-sky-500 bg-sky-500/7 hover:bg-sky-500/12",
  success: "border-l-emerald-500 bg-emerald-500/7 hover:bg-emerald-500/12",
  warning: "border-l-amber-500 bg-amber-500/10 hover:bg-amber-500/15",
  danger: "border-l-rose-500 bg-rose-500/8 hover:bg-rose-500/14",
  muted: "border-l-border bg-muted/45 text-muted-foreground",
}

/** One value or one label: colour alone, since the row may carry the wash. */
const TEXT_CLASSES: Record<TideEmphasis, string> = {
  info: "text-sky-700 dark:text-sky-300",
  success: "text-emerald-700 dark:text-emerald-300",
  warning: "text-amber-700 dark:text-amber-300",
  danger: "text-rose-700 dark:text-rose-300",
  muted: "text-muted-foreground",
}

/** The card of an open record, where the row's left edge becomes the card's. */
const CARD_CLASSES: Record<TideEmphasis, string> = {
  info: "border-l-4 border-l-sky-500",
  success: "border-l-4 border-l-emerald-500",
  warning: "border-l-4 border-l-amber-500",
  danger: "border-l-4 border-l-rose-500",
  muted: "border-l-4 border-l-border",
}

/**
 * A verdict this renderer knows how to draw, or nothing.
 *
 * An unknown name is dropped rather than rendered as a missing class: a server
 * one version ahead may name an emphasis this bundle has never heard of, and
 * an unpainted record reads better than a broken one.
 */
export function asEmphasis(value: unknown): TideEmphasis | undefined {
  return typeof value === "string" && EMPHASIS_VALUES.includes(value)
    ? (value as TideEmphasis)
    : undefined
}

/** What the rules said about the record as a whole. */
export function recordEmphasis(
  record: TideRecord | undefined,
): TideEmphasis | undefined {
  return asEmphasis(record?._tide?.appearance?.record)
}

/** What the rules said about one of its fields. */
export function fieldEmphasis(
  record: TideRecord | undefined,
  name: string,
): TideEmphasis | undefined {
  return asEmphasis(record?._tide?.appearance?.fields?.[name])
}

export function rowEmphasisClass(
  emphasis: TideEmphasis | undefined,
): string | undefined {
  return emphasis && ROW_CLASSES[emphasis]
}

export function textEmphasisClass(
  emphasis: TideEmphasis | undefined,
): string | undefined {
  return emphasis && TEXT_CLASSES[emphasis]
}

export function cardEmphasisClass(
  emphasis: TideEmphasis | undefined,
): string | undefined {
  return emphasis && CARD_CLASSES[emphasis]
}
