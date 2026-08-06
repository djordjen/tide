import { useQuery } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationColumn,
  TideRecord,
} from "@/lib/contracts"
import {
  formatCellValue,
  formatReferenceDisplay,
} from "@/lib/format"
import { cn } from "@/lib/utils"

interface TideDisplayValueProps {
  api: TideApi
  column: TidePresentationColumn
  record: TideRecord
  className?: string
  wrap?: boolean
}

export function TideDisplayValue({
  api,
  column,
  record,
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
  }

  if (column.field_type === "choice" && text) {
    return (
      <Badge
        className={cn("max-w-full", !wrap && "truncate", className)}
        variant="secondary"
        title={text}
      >
        {text}
      </Badge>
    )
  }
  return (
    <span
      className={cn(
        "block min-w-0",
        wrap ? "break-words whitespace-pre-wrap" : "truncate",
        protectedFields.includes(column.name) &&
          "italic text-muted-foreground",
        referenceQuery.isPending && "text-muted-foreground",
        className,
      )}
      title={text}
    >
      {text || "—"}
    </span>
  )
}
