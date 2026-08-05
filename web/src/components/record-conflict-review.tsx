import { useEffect, useRef } from "react"
import {
  AlertTriangle,
  Check,
  GitMerge,
  LockKeyhole,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type {
  TideFormPresentation,
  TidePresentationFormCollection,
} from "@/lib/contracts"
import {
  conflictFieldLabel,
  resolveRecordConflict,
  type TideConflictChoice,
  type TideConflictDisposition,
  type TideRecordConflict,
} from "@/lib/conflicts"
import { useDialogFocus } from "@/lib/dialog-focus"
import { formatCellValue } from "@/lib/format"
import { cn } from "@/lib/utils"

interface RecordConflictReviewProps {
  form: TideFormPresentation
  collections: TidePresentationFormCollection[]
  conflict: TideRecordConflict
  lockedFields: ReadonlySet<string>
  choices: Readonly<Record<string, TideConflictChoice>>
  onChoice: (name: string, choice: TideConflictChoice) => void
  onContinueEditing: () => void
  onReloadCurrent: () => void
  onApply: () => void
}

export function RecordConflictReview({
  form,
  collections,
  conflict,
  lockedFields,
  choices,
  onChoice,
  onContinueEditing,
  onReloadCurrent,
  onApply,
}: RecordConflictReviewProps) {
  const dialogRef = useRef<HTMLElement>(null)
  const keepFocusInDialog = useDialogFocus(dialogRef)
  const resolution = resolveRecordConflict(conflict, choices)
  const retainedSafe = conflict.rebaseFields.filter(
    (name) => !lockedFields.has(name),
  ).length

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        event.stopPropagation()
        onContinueEditing()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [onContinueEditing])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-3 backdrop-blur-[2px] md:p-6"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) {
          onContinueEditing()
        }
      }}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="record-conflict-title"
        tabIndex={-1}
        className="flex max-h-[min(800px,calc(100vh-1.5rem))] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border bg-card shadow-2xl md:max-h-[calc(100vh-3rem)]"
        onKeyDown={keepFocusInDialog}
      >
        <header className="shrink-0 border-b px-5 py-4 md:px-6">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 rounded-xl bg-amber-100 p-2 text-amber-700 dark:bg-amber-950 dark:text-amber-300">
              <AlertTriangle className="size-5" />
            </div>
            <div className="min-w-0">
              <h2
                id="record-conflict-title"
                className="text-xl font-semibold tracking-tight"
              >
                Record changed elsewhere
              </h2>
              <p className="mt-1 text-sm leading-5 text-muted-foreground">
                Compare the value you opened, the current server value,
                and your draft. Choose a value for every overlapping
                change.
              </p>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge variant="warning">
              {resolution.unresolvedFields.length} decision
              {resolution.unresolvedFields.length === 1 ? "" : "s"} remaining
            </Badge>
            <Badge variant="success">
              {retainedSafe} safe draft change
              {retainedSafe === 1 ? "" : "s"}
            </Badge>
            {lockedFields.size > 0 ? (
              <Badge variant="outline">
                <LockKeyhole className="size-3" />
                {lockedFields.size} now locked
              </Badge>
            ) : null}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-auto">
          {conflict.fields.length === 0 ? (
            <div className="flex min-h-48 flex-col items-center justify-center px-6 text-center">
              <GitMerge className="mb-3 size-7 text-muted-foreground" />
              <p className="font-medium">No field values overlap</p>
              <p className="mt-1 max-w-lg text-sm text-muted-foreground">
                The version changed, but the editable values are already
                compatible. Apply the resolution to continue on the latest
                version.
              </p>
            </div>
          ) : (
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
                <tr>
                  {[
                    "Field",
                    "Original",
                    "Current",
                    "Your draft",
                    "Resolution",
                  ].map((label) => (
                    <th
                      key={label}
                      className="border-r border-b px-4 py-3 text-left text-xs font-semibold last:border-r-0"
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {conflict.fields.map((field) => {
                  const label = conflictFieldLabel(
                    form,
                    collections,
                    field.name,
                  )
                  const locked = lockedFields.has(field.name)
                  return (
                    <tr
                      key={field.name}
                      className={cn(
                        "border-b last:border-b-0",
                        field.disposition === "conflict" &&
                          "bg-amber-50/45 dark:bg-amber-950/15",
                      )}
                    >
                      <td className="border-r px-4 py-3 align-top font-medium">
                        <div className="flex items-center gap-2">
                          <span>{label}</span>
                          {locked ? (
                            <LockKeyhole
                              className="size-3.5 text-muted-foreground"
                              aria-label={`${label} is now locked`}
                            />
                          ) : null}
                        </div>
                        <ConflictDispositionBadge
                          disposition={field.disposition}
                        />
                      </td>
                      {[field.original, field.current, field.draft].map(
                        (value, index) => (
                          <td
                            key={index}
                            className="max-w-72 border-r px-4 py-3 align-top last:border-r-0"
                          >
                            <span className="block max-h-24 overflow-auto break-words whitespace-pre-wrap">
                              {formatConflictValue(
                                form,
                                collections,
                                field.name,
                                value,
                              )}
                            </span>
                          </td>
                        ),
                      )}
                      <td className="px-4 py-3 align-top">
                        {field.disposition === "conflict" ? (
                          <div className="flex flex-wrap gap-2">
                            <Button
                              aria-label={`Use current ${label}`}
                              aria-pressed={choices[field.name] === "current"}
                              size="sm"
                              variant={
                                choices[field.name] === "current"
                                  ? "default"
                                  : "outline"
                              }
                              onClick={() =>
                                onChoice(field.name, "current")
                              }
                            >
                              {choices[field.name] === "current" ? (
                                <Check />
                              ) : null}
                              Use current
                            </Button>
                            {!locked ? (
                              <Button
                                aria-label={`Use my ${label}`}
                                aria-pressed={choices[field.name] === "draft"}
                                size="sm"
                                variant={
                                  choices[field.name] === "draft"
                                    ? "default"
                                    : "outline"
                                }
                                onClick={() =>
                                  onChoice(field.name, "draft")
                                }
                              >
                                {choices[field.name] === "draft" ? (
                                  <Check />
                                ) : null}
                                Use mine
                              </Button>
                            ) : null}
                          </div>
                        ) : (
                          <ConflictOutcome
                            disposition={field.disposition}
                            locked={locked}
                          />
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t bg-muted/25 px-5 py-4 md:px-6">
          <Button
            autoFocus
            variant="outline"
            onClick={onContinueEditing}
          >
            Continue editing
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onReloadCurrent}>
              Reload current
            </Button>
            <Button
              disabled={!resolution.complete}
              onClick={onApply}
            >
              <GitMerge />
              Apply resolution
            </Button>
          </div>
        </footer>
      </section>
    </div>
  )
}

function ConflictDispositionBadge({
  disposition,
}: {
  disposition: TideConflictDisposition
}) {
  const label = {
    your_change: "Your change",
    current_change: "Server change",
    same_change: "Same change",
    conflict: "Overlap",
  }[disposition]
  return (
    <Badge
      className="mt-2"
      variant={disposition === "conflict" ? "warning" : "outline"}
    >
      {label}
    </Badge>
  )
}

function ConflictOutcome({
  disposition,
  locked,
}: {
  disposition: TideConflictDisposition
  locked: boolean
}) {
  if (locked && disposition === "your_change") {
    return (
      <span className="text-xs leading-5 text-muted-foreground">
        Use current; workflow rules now lock this field.
      </span>
    )
  }
  const label = {
    your_change: "Keep your change",
    current_change: "Use current",
    same_change: "Already the same",
    conflict: "",
  }[disposition]
  return <span className="text-xs text-muted-foreground">{label}</span>
}

function formatConflictValue(
  form: TideFormPresentation,
  collections: TidePresentationFormCollection[],
  name: string,
  value: unknown,
): string {
  const collection = collections.find((item) => item.name === name)
  if (collection) {
    const count = Array.isArray(value) ? value.length : 0
    return `${count} ${count === 1 ? "row" : "rows"}`
  }
  const field = form.fields[name]
  if (field) {
    return formatCellValue(field, value) || "—"
  }
  if (value && typeof value === "object") {
    return "Record"
  }
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value)
}
