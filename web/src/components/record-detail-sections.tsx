import type { CSSProperties } from "react"
import { LockKeyhole, Rows3 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { TideDisplayValue } from "@/components/tide-display-value"
import type { TideApi } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  fieldCellClass,
  fieldGroupClass,
  fieldLabelRowClass,
  fieldLabelTextClass,
  readOnlyValueClass,
  sectionCaptionClass,
} from "./form-field"
import type {
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormGroup,
  TideRecord,
} from "@/lib/contracts"

/**
 * The read-only halves of a record detail: one group of fields, one
 * collection, and the placeholder shown while the record loads.
 *
 * They take props and return markup -- no state, no effects, no request.
 * That is what makes them separable from the editor they sit inside,
 * whose thirteen pieces of state every handler reaches into at once.
 */

export function DetailGroup({
  api,
  form,
  record,
  section,
  writable,
}: {
  api: TideApi
  form: TideFormPresentation
  record: TideRecord
  section: TidePresentationFormGroup
  writable: ReadonlySet<string>
}) {
  return (
    <section>
      <h2 className={sectionCaptionClass}>{section.label}</h2>
      <div className="space-y-2.5 px-4 py-4 md:px-5">
        {section.rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className="tide-form-row grid gap-y-3 gap-x-6"
            style={
              {
                "--tide-form-columns": row.length,
              } as CSSProperties
            }
          >
            {row.map((name) => {
              const field = form.fields[name]
              const fieldWritable = writable.has(name)
              return (
                <div
                  key={name}
                  className={`${fieldCellClass} ${fieldGroupClass}`}
                >
                  <div className={fieldLabelRowClass}>
                    <span className={fieldLabelTextClass}>{field.label}</span>
                    {!fieldWritable ? (
                      <LockKeyhole
                        className="size-3 text-muted-foreground/55"
                        aria-label={`${field.label} is read-only`}
                      />
                    ) : null}
                  </div>
                  {/* Left-aligned like every other form value: a number
                      flushed to the far column edge floats away from its
                      label, and the editor never does that. Right
                      alignment belongs to tables, where digits line up
                      down a column. */}
                  <TideDisplayValue
                    api={api}
                    column={field}
                    record={record}
                    wrap
                    className={cn(
                      readOnlyValueClass,
                      field.alignment === "right" && "tabular-nums",
                    )}
                  />
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}


export function DetailCollection({
  api,
  record,
  section,
  heading = true,
}: {
  api: TideApi
  record: TideRecord
  section: TidePresentationFormCollection
  /** False when a tab already names the collection. */
  heading?: boolean
}) {
  const protectedFields = record._tide?.protected_fields ?? []
  const protectedCollection = protectedFields.includes(section.name)
  const raw = record[section.name]
  const rows = Array.isArray(raw) ? (raw as TideRecord[]) : []

  return (
    <section className="min-w-0 overflow-hidden rounded-xl border bg-muted/20">
      {heading ? (
        <h2 className={sectionCaptionClass}>{section.label}</h2>
      ) : null}
      <div className="p-3">
        <div className="mb-3 flex items-center justify-end gap-3">
          <Badge variant="outline">
            {protectedCollection
              ? "Protected"
              : `${rows.length} ${rows.length === 1 ? "row" : "rows"}`}
          </Badge>
        </div>
        <div className="max-h-80 overflow-auto rounded-lg border bg-background">
        {protectedCollection ? (
          <div className="flex min-h-44 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LockKeyhole className="size-4" />
            This collection is protected for the current identity.
          </div>
        ) : rows.length === 0 ? (
          <div className="flex min-h-44 flex-col items-center justify-center text-sm text-muted-foreground">
            <Rows3 className="mb-2 size-5" />
            No {section.label}
          </div>
        ) : (
          <table className="w-full min-w-max border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
              <tr>
                {section.columns.map((column) => (
                  <th
                    key={column.name}
                    className={cn(
                      "border-r border-b px-3 py-2.5 text-xs font-semibold whitespace-nowrap last:border-r-0",
                      column.alignment === "right"
                        ? "text-right"
                        : column.alignment === "center"
                          ? "text-center"
                          : "text-left",
                    )}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr
                  key={String(row[section.columns[0]?.name] ?? rowIndex)}
                  className="border-b last:border-b-0 hover:bg-accent/25"
                >
                  {section.columns.map((column) => (
                    <td
                      key={column.name}
                      className={cn(
                        "max-w-96 border-r px-3 py-2.5 last:border-r-0",
                        column.alignment === "right"
                          ? "text-right tabular-nums"
                          : column.alignment === "center"
                            ? "text-center"
                            : "text-left",
                      )}
                    >
                      <TideDisplayValue
                        api={api}
                        column={column}
                        record={row}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </div>
      </div>
    </section>
  )
}


export function DetailSkeleton() {
  return (
    <div className="space-y-5 p-5">
      <div>
        <Skeleton className="mb-3 h-4 w-24" />
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-17 rounded-xl" />
          ))}
        </div>
      </div>
      <Skeleton className="h-52 rounded-xl" />
    </div>
  )
}
