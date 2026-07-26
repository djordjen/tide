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
  const referenceQuery = useQuery({
    queryKey: ["reference-display", reference?.entity, value],
    enabled:
      reference !== null &&
      reference !== undefined &&
      value !== null &&
      value !== undefined &&
      !protectedFields.includes(column.name),
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
  if (reference && referenceQuery.data) {
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
