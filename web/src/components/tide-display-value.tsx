import { useQuery } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationColumn,
  TidePresentationManifest,
  TideRecord,
} from "@/lib/contracts"
import { fieldEmphasis, textEmphasisClass } from "@/lib/emphasis"
import {
  formatCellValue,
  formatReferenceDisplay,
} from "@/lib/format"
import {
  referenceLinkClick,
  referenceRecordHref,
} from "@/lib/reference-link"
import { cn } from "@/lib/utils"

interface TideDisplayValueProps {
  api: TideApi
  column: TidePresentationColumn
  record: TideRecord
  /** When given, a resolved reference renders as a door to its record. */
  views?: TidePresentationManifest["views"]
  /** -1 inside the data grid, whose roving tab stop owns the keyboard. */
  linkTabIndex?: number
  className?: string
  wrap?: boolean
}

/**
 * A choice value wears a soft tint chosen by its text, the same on every
 * screen of every application. The tint claims identity, never judgement --
 * the framework cannot know whether an application's `posted` is good news,
 * so the buckets carry no success green and no danger red, and a value's
 * color means only "these rows are in the same state".
 */
const TINT_NAMES = [
  "sky",
  "teal",
  "violet",
  "amber",
  "slate",
  "indigo",
] as const

const TINT_CLASSES: Record<string, string> = {
  sky: "border border-sky-200 bg-sky-100/80 text-sky-950 dark:border-sky-400/25 dark:bg-sky-400/15 dark:text-sky-200",
  teal: "border border-teal-200 bg-teal-100/80 text-teal-950 dark:border-teal-400/25 dark:bg-teal-400/15 dark:text-teal-200",
  violet:
    "border border-violet-200 bg-violet-100/80 text-violet-950 dark:border-violet-400/25 dark:bg-violet-400/15 dark:text-violet-200",
  amber:
    "border border-amber-200 bg-amber-100/80 text-amber-950 dark:border-amber-400/25 dark:bg-amber-400/15 dark:text-amber-200",
  slate:
    "border border-slate-200 bg-slate-100/80 text-slate-950 dark:border-slate-400/25 dark:bg-slate-400/15 dark:text-slate-200",
  indigo:
    "border border-indigo-200 bg-indigo-100/80 text-indigo-950 dark:border-indigo-400/25 dark:bg-indigo-400/15 dark:text-indigo-200",
}

function valueTint(text: string): (typeof TINT_NAMES)[number] {
  let hash = 0
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) | 0
  }
  return TINT_NAMES[Math.abs(hash) % TINT_NAMES.length]
}

export function TideDisplayValue({
  api,
  column,
  record,
  views,
  linkTabIndex,
  className,
  wrap = false,
}: TideDisplayValueProps) {
  const value = record[column.name]
  const protectedFields = record._tide?.protected_fields ?? []
  const reference = column.reference
  const withheld = protectedFields.includes(column.name)
  // Resolved with the page that carried this row, so the common case costs
  // nothing. It describes the row as the server sent it -- anything that
  // edits a value has to leave the name behind with the value it named.
  // A withheld field takes none: a name is a value, and the whole point of
  // withholding one is that this reader does not get it.
  const resolved = withheld
    ? undefined
    : record._tide?.references?.[column.name]
  const referenceQuery = useQuery({
    queryKey: ["reference-display", reference?.entity, value],
    enabled:
      resolved === undefined &&
      reference !== null &&
      reference !== undefined &&
      value !== null &&
      value !== undefined &&
      !withheld,
    queryFn: ({ signal }) => {
      if (!reference) {
        throw new Error("reference contract missing")
      }
      return api.getReference(reference, value, signal)
    },
    staleTime: 300_000,
    retry: false,
  })

  let text = formatCellValue(column, value, protectedFields)
  if (reference && resolved !== undefined) {
    text = resolved
  } else if (reference && referenceQuery.data) {
    text = formatReferenceDisplay(reference, referenceQuery.data)
  } else if (reference && referenceQuery.isLoading) {
    // The key is the database's spelling of the reference, not its name;
    // showing it for a frame teaches people to read keys. A failed fetch
    // falls back to the key, which at least is true.
    text = "…"
  }

  if (column.field_type === "choice" && text) {
    const tint = valueTint(text)
    return (
      // The wrapper takes the cell's layout classes -- alignment, the
      // read-only baseline padding -- so they can never leak inside the
      // chip; padding inside the chip is what made its text sit low.
      <span className={cn("block min-w-0", className)}>
        <Badge
          className={cn("max-w-full", TINT_CLASSES[tint], !wrap && "truncate")}
          variant="secondary"
          data-tint={tint}
          title={text}
        >
          {text}
        </Badge>
      </span>
    )
  }
  const recordHref =
    reference && !withheld && text && text !== "…"
      ? referenceRecordHref(views, reference, value)
      : null
  if (recordHref) {
    // The resolved name is itself the door, the way the reference
    // application draws its grids: in place, one history entry, and the
    // record screen's Close walks back to exactly here. A modified click
    // still opens a tab, because this is a real anchor.
    return (
      <span
        className={cn(
          "block min-w-0",
          wrap ? "break-words whitespace-pre-wrap" : "truncate",
          className,
        )}
        title={text}
      >
        <a
          href={recordHref}
          tabIndex={linkTabIndex}
          className="rounded-sm underline decoration-muted-foreground/50 underline-offset-2 outline-none hover:decoration-current focus-visible:ring-2 focus-visible:ring-ring/40"
          onClick={(event) => referenceLinkClick(event, recordHref)}
        >
          {text}
        </a>
      </span>
    )
  }

  // What the application's own rules made of this field on this record. The
  // value is where a rule lands in a grid: a list is nothing but values.
  const emphasis = fieldEmphasis(record, column.name)
  return (
    <span
      className={cn(
        "block min-w-0",
        wrap ? "break-words whitespace-pre-wrap" : "truncate",
        protectedFields.includes(column.name) &&
          "italic text-muted-foreground",
        referenceQuery.isLoading && "text-muted-foreground",
        textEmphasisClass(emphasis),
        emphasis && "font-medium",
        className,
      )}
      data-emphasis={emphasis}
      title={text}
    >
      {text || "—"}
    </span>
  )
}
