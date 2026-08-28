import { useQuery } from "@tanstack/react-query"
import { History, LoaderCircle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideAuditEvent,
  TideAuditFieldChange,
  TideBrowsePresentation,
  TideFormPresentation,
} from "@/lib/contracts"

/**
 * The record's audit trail, as one tab on the panel below the record.
 *
 * Read-only by nature and by contract: the wire already decided what may be
 * shown -- redacted values arrive without their values, and this renders the
 * decision rather than re-making it. The server orders events newest first
 * and bounds them; the panel repeats neither judgement.
 */

const OPERATION_LABELS: Record<string, string> = {
  create: "Created",
  update: "Updated",
  delete: "Deleted",
}

export function RecordHistory({
  api,
  view,
  form,
  identity,
}: {
  api: TideApi
  view: TideBrowsePresentation
  form: TideFormPresentation
  identity: unknown
}) {
  const query = useQuery({
    queryKey: ["record-history", view.view, identity],
    queryFn: ({ signal }) => api.recordHistory(view, identity, signal),
    staleTime: 15_000,
  })
  const events = query.data?.events ?? []
  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The record's history could not be loaded.")
        : null

  return (
    <section className="min-w-0 overflow-hidden rounded-xl border bg-muted/20">
      <div className="p-3">
        <div className="mb-3 flex items-center justify-end gap-3">
          {query.isFetching ? (
            <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
          ) : null}
          {query.data ? (
            <Badge variant="outline">
              {events.length} {events.length === 1 ? "event" : "events"}
            </Badge>
          ) : null}
        </div>
        <div className="max-h-80 overflow-auto rounded-lg border bg-background">
          {error ? (
            <div
              role="alert"
              className="flex min-h-44 flex-col items-center justify-center gap-3 px-4 text-center text-sm text-destructive"
            >
              <span>{error.message}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => query.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : query.isPending ? (
            <div className="flex min-h-44 items-center justify-center">
              <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : events.length === 0 ? (
            <div className="flex min-h-44 flex-col items-center justify-center text-sm text-muted-foreground">
              <History className="mb-2 size-5" />
              No history recorded for this record.
            </div>
          ) : (
            <table className="w-full min-w-max border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
                <tr>
                  {["When", "Event", "Changes", "By", "Channel"].map(
                    (column) => (
                      <th
                        key={column}
                        className="border-r border-b px-3 py-2.5 text-left text-xs font-semibold whitespace-nowrap last:border-r-0"
                      >
                        {column}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr
                    key={event.event_id}
                    title={`Correlation ${event.correlation_id}`}
                    className="border-b align-top last:border-b-0 hover:bg-accent/25"
                  >
                    <td className="border-r px-3 py-2.5 whitespace-nowrap tabular-nums">
                      {formatOccurred(event.started_at)}
                    </td>
                    <td className="border-r px-3 py-2.5">
                      <span className="font-medium">{eventName(event)}</span>
                      {eventQualifier(event) ? (
                        <span className="block text-xs text-muted-foreground">
                          {eventQualifier(event)}
                        </span>
                      ) : null}
                    </td>
                    <td className="max-w-96 border-r px-3 py-2.5">
                      {event.kind === "record" ? (
                        event.changes.map((change) => (
                          <div key={change.field} className="break-words">
                            {changeLine(change, fieldLabel(form, change.field))}
                          </div>
                        ))
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="border-r px-3 py-2.5 whitespace-nowrap">
                      {event.principal}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-muted-foreground">
                      {event.channel}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {query.data ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Newest first · Protected values stay redacted
          </p>
        ) : null}
      </div>
    </section>
  )
}

function eventName(event: TideAuditEvent): string {
  if (event.kind === "action") {
    const outcome = event.outcome
      ? ` · ${event.outcome.charAt(0).toUpperCase()}${event.outcome.slice(1)}`
      : ""
    return `${event.action ?? "Action"}${outcome}`
  }
  return OPERATION_LABELS[event.operation ?? ""] ?? (event.operation ?? "Record")
}

function eventQualifier(event: TideAuditEvent): string | null {
  if (event.kind === "action") {
    return event.error_code
  }
  return event.source && event.source !== "user" ? `via ${event.source}` : null
}

function fieldLabel(form: TideFormPresentation, field: string): string {
  // The author's word where one was declared; history can also carry fields
  // the form no longer shows, which keep their stored name.
  return form.fields[field]?.label ?? field
}

function changeLine(change: TideAuditFieldChange, label: string): string {
  if (change.value_mode === "redacted") {
    return `${label}: [redacted]`
  }
  if (change.value_mode === "field_only") {
    return label
  }
  const before = change.before_present ? changeValue(change.before) : "[absent]"
  const after = change.after_present ? changeValue(change.after) : "[absent]"
  return `${label}: ${before} → ${after}`
}

function changeValue(value: unknown): string {
  const text = value === null || value === undefined ? "null" : String(value)
  return text.length <= 60 ? text : `${text.slice(0, 57)}...`
}

/** The terminal's timestamp, localized: day.month.year, then the clock. */
function formatOccurred(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  const pad = (part: number) => String(part).padStart(2, "0")
  return (
    `${pad(parsed.getDate())}.${pad(parsed.getMonth() + 1)}.` +
    `${parsed.getFullYear()} ${pad(parsed.getHours())}:` +
    `${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`
  )
}
