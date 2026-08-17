import { useEffect, useMemo, useState } from "react"
import { CirclePlus, ListChecks, Trash2 } from "lucide-react"

import {
  formEditorId,
  RecordFormEditor,
} from "@/components/record-form-editor"
import { TideDisplayValue } from "@/components/tide-display-value"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationFormCollection,
  TidePresentationManifest,
  TideRecord,
} from "@/lib/contracts"
import {
  collectionEditorForm,
  newCollectionDraft,
  validateCollectionDrafts,
  type TideFormErrors,
} from "@/lib/form-draft"
import { cn } from "@/lib/utils"
import { sectionCaptionClass } from "./form-field"

interface EditableCollectionProps {
  api: TideApi
  section: TidePresentationFormCollection
  forms: TidePresentationManifest["forms"]
  rows: TideRecord[]
  errors: TideFormErrors[]
  editable: boolean
  disabled: boolean
  /** False when a tab already names the collection, so the panel says it once. */
  heading?: boolean
  onRowsChange: (rows: TideRecord[]) => void
  onErrorsChange: (errors: TideFormErrors[]) => void
}

export function EditableCollection({
  api,
  section,
  forms,
  rows,
  errors,
  editable,
  disabled,
  heading = true,
  onRowsChange,
  onErrorsChange,
}: EditableCollectionProps) {
  const form = useMemo(() => collectionEditorForm(section), [section])
  const [selectedIndex, setSelectedIndex] = useState<number | null>(
    rows.length > 0 ? 0 : null,
  )
  const editableFields = useMemo(
    () =>
      new Set(
        Object.values(form?.fields ?? {})
          .filter((field) => field.writable)
          .map((field) => field.name),
      ),
    [form],
  )

  useEffect(() => {
    if (rows.length === 0) {
      setSelectedIndex(null)
    } else if (
      selectedIndex === null ||
      selectedIndex >= rows.length
    ) {
      setSelectedIndex(rows.length - 1)
    }
  }, [rows.length, selectedIndex])

  useEffect(() => {
    if (!form) {
      return
    }
    const invalidIndex = errors.findIndex(
      (item) => Object.keys(item).length > 0,
    )
    if (invalidIndex < 0) {
      return
    }
    setSelectedIndex(invalidIndex)
    const fieldName = Object.keys(errors[invalidIndex] ?? {})[0]
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        document
          .querySelector<HTMLElement>(
            `[data-tide-collection="${section.name}"]`,
          )
          ?.querySelector<HTMLElement>(
            `#${formEditorId(form, fieldName, section.name)}`,
          )
          ?.focus()
      }),
    )
  }, [errors, form, section.name])

  if (!form) {
    return null
  }

  const selected =
    selectedIndex === null ? null : rows[selectedIndex] ?? null
  const actions = new Set(section.actions ?? [])

  function updateSelected(values: Record<string, unknown>) {
    if (selectedIndex === null) {
      return
    }
    const next = rows.map((row, index) =>
      index === selectedIndex ? { ...row, ...values } : row,
    )
    onRowsChange(next)
    const nextErrors = [...errors]
    nextErrors[selectedIndex] = {}
    onErrorsChange(nextErrors)
  }

  function addLine() {
    const next = [...rows, newCollectionDraft(section, rows)]
    onRowsChange(next)
    onErrorsChange([...errors, {}])
    setSelectedIndex(next.length - 1)
  }

  function applyLine() {
    if (selectedIndex === null || !selected) {
      return
    }
    const lineErrors = validateCollectionDrafts(section, [selected])[0] ?? {}
    const next = [...errors]
    next[selectedIndex] = lineErrors
    onErrorsChange(next)
    if (Object.keys(lineErrors).length === 0) {
      requestAnimationFrame(() => {
        document
          .querySelector<HTMLElement>(
            `[data-tide-collection="${section.name}"] tbody tr:nth-child(${selectedIndex + 1})`,
          )
          ?.focus()
      })
    }
  }

  function removeLine() {
    if (selectedIndex === null) {
      return
    }
    onRowsChange(rows.filter((_, index) => index !== selectedIndex))
    onErrorsChange(errors.filter((_, index) => index !== selectedIndex))
    setSelectedIndex(
      rows.length <= 1 ? null : Math.min(selectedIndex, rows.length - 2),
    )
  }

  return (
    // The whole collection -- table, line editor, line actions -- is one
    // recessed region, so where the record's own fields end and its rows
    // begin is a visible boundary rather than an inference.
    <section
      className="min-w-0 overflow-hidden rounded-xl border bg-muted/20"
      data-tide-collection={section.name}
    >
      {heading ? (
        <h2 className={sectionCaptionClass}>{section.label}</h2>
      ) : null}
      <div className="p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          Select a row to edit its details. Calculated values are finalized
          by the server when the record is saved.
        </p>
        <Badge variant="outline">
          {rows.length} draft {rows.length === 1 ? "row" : "rows"}
        </Badge>
      </div>

      {/* The table takes the height its rows need, up to the cap: a one-line
          draft used to sit above a hundred pixels of reserved emptiness,
          pushing its own editors below the fold. Only the empty state keeps
          a minimum, so "No Lines" has room to say so. */}
      <div className="max-h-80 overflow-auto rounded-lg border bg-background">
        {rows.length === 0 ? (
          <div className="flex min-h-44 flex-col items-center justify-center text-sm text-muted-foreground">
            <ListChecks className="mb-2 size-5" />
            {/* An empty screen is an invitation to act: name the control
                that resolves it, in the manifest's own words. */}
            {actions.has("add")
              ? `No ${section.label} yet — Add ${section.record_label} starts one.`
              : `No ${section.label}`}
          </div>
        ) : (
          <table className="w-full min-w-max border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
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
              {rows.map((row, rowIndex) => {
                const active = rowIndex === selectedIndex
                const identityField = section.identity_field
                return (
                  <tr
                    key={String(
                      (identityField && row[identityField]) ??
                        `draft-${rowIndex}`,
                    )}
                    tabIndex={0}
                    aria-selected={active}
                    className={cn(
                      "cursor-pointer border-b outline-none last:border-b-0 hover:bg-accent/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/30",
                      active && "bg-primary/10",
                    )}
                    onClick={() => setSelectedIndex(rowIndex)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault()
                        setSelectedIndex(rowIndex)
                      }
                    }}
                  >
                    {section.columns.map((column, columnIndex) => (
                      <td
                        key={column.name}
                        className={cn(
                          "max-w-96 border-r px-3 py-2.5 last:border-r-0",
                          // The same primary edge the editor card below
                          // wears, so the highlighted row and the fields
                          // editing it read as one thing. Every first cell
                          // carries the width, so selection never shifts
                          // the columns.
                          columnIndex === 0 &&
                            "border-l-2 border-l-transparent",
                          columnIndex === 0 &&
                            active &&
                            "border-l-primary",
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
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {selected ? (
        // The editor of the highlighted row, not another form section: an
        // inner card on the recessed region, wearing the selection's primary
        // as a left accent so the table row and the fields editing it read
        // as one thing. Its group caption renders as the card's own band.
        <div className="mt-3 overflow-hidden rounded-lg border border-l-2 border-l-primary/55 bg-background shadow-xs">
          <RecordFormEditor
            api={api}
            form={form}
            forms={forms}
            draft={selected}
            editableFields={editable ? editableFields : new Set()}
            errors={errors[selectedIndex ?? 0] ?? {}}
            disabled={disabled || !editable}
            idScope={section.name}
            headingSuffix={`· row ${(selectedIndex ?? 0) + 1} of ${rows.length}`}
            onChange={(name, value) =>
              updateSelected({ [name]: value })
            }
            onApplyValues={updateSelected}
          />
        </div>
      ) : null}

      {editable ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {actions.has("add") ? (
            <Button
              type="button"
              variant="outline"
              disabled={disabled}
              onClick={addLine}
            >
              <CirclePlus />
              Add {section.record_label}
            </Button>
          ) : null}
          {actions.has("apply") ? (
            <Button
              type="button"
              variant="outline"
              disabled={disabled || selectedIndex === null}
              onClick={applyLine}
            >
              <ListChecks />
              Apply {section.record_label}
            </Button>
          ) : null}
          {actions.has("remove") ? (
            <Button
              type="button"
              variant="outline"
              disabled={disabled || selectedIndex === null}
              onClick={removeLine}
            >
              <Trash2 />
              Remove {section.record_label}
            </Button>
          ) : null}
        </div>
      ) : null}
      </div>
    </section>
  )
}
