import { useMemo, useRef, useState } from "react"
import { CircleAlert, X } from "lucide-react"

import { GridCellEditor } from "@/components/grid-cell-editor"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDialogFocus } from "@/lib/dialog-focus"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideBrowseMassUpdate,
  TideBrowsePresentation,
  TideFormPresentation,
  TideMassUpdateOutcome,
  TideMassUpdateTarget,
  TidePresentationFormField,
  TideRecord,
} from "@/lib/contracts"

/**
 * One field and one value for the selected rows, answered row by row.
 *
 * Scalar fields only, exactly like the terminal's dialog: a mass-assigned
 * reference would skip the `on_select` assignments a hand pick applies, so
 * offering it as a click-path would quietly produce rows a person's pick
 * would have filled differently; and a staged file claims exactly once.
 * The service door accepts both -- the dialogs abstain.
 */
const MASS_ASSIGNABLE_TYPES = new Set([
  "boolean",
  "choice",
  "date",
  "datetime",
  "decimal",
  "integer",
  "string",
  "uuid",
])

export function massAssignableFields(
  form: TideFormPresentation | null,
): TidePresentationFormField[] {
  if (!form) {
    return []
  }
  return Object.values(form.fields).filter(
    (field) =>
      field.writable && MASS_ASSIGNABLE_TYPES.has(field.field_type),
  )
}

interface MassUpdateDialogProps {
  api: TideApi
  view: TideBrowsePresentation
  massUpdate: TideBrowseMassUpdate
  form: TideFormPresentation
  records: TideRecord[]
  selected: ReadonlySet<string>
  onClose: () => void
  /** Called after any pass that updated at least one row. */
  onApplied: () => void
}

interface MassUpdateReport {
  updated: number
  total: number
  refusals: TideMassUpdateOutcome[]
}

export function MassUpdateDialog({
  api,
  view,
  massUpdate,
  form,
  records,
  selected,
  onClose,
  onApplied,
}: MassUpdateDialogProps) {
  const dialogRef = useRef<HTMLElement>(null)
  const keepFocusInDialog = useDialogFocus(dialogRef)
  const fields = useMemo(() => massAssignableFields(form), [form])
  const [fieldName, setFieldName] = useState("")
  const [value, setValue] = useState<unknown>("")
  const [clearField, setClearField] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<MassUpdateReport | null>(null)

  const field = fields.find((candidate) => candidate.name === fieldName)
  const labelOf = useMemo(() => {
    const labels = new Map<string, string>()
    const first = view.columns[0]?.name
    for (const record of records) {
      const identity = String(record[view.identity_field])
      const display = first ? record[first] : null
      labels.set(
        identity,
        display === null || display === undefined
          ? identity
          : String(display),
      )
    }
    return (identity: number | string) =>
      labels.get(String(identity)) ?? String(identity)
  }, [records, view.columns, view.identity_field])

  const selectedRecords = useMemo(
    () =>
      records.filter((record) =>
        selected.has(String(record[view.identity_field])),
      ),
    [records, selected, view.identity_field],
  )

  const buildTargets = (
    identities?: ReadonlySet<string>,
  ): {
    targets: TideMassUpdateTarget[]
    unreadable: TideMassUpdateOutcome[]
  } => {
    const targets: TideMassUpdateTarget[] = []
    const unreadable: TideMassUpdateOutcome[] = []
    for (const record of selectedRecords) {
      const identity = record[view.identity_field] as number | string
      if (identities && !identities.has(String(identity))) {
        continue
      }
      if (massUpdate.version_field === null) {
        targets.push({ identity })
        continue
      }
      const token = record[massUpdate.version_field]
      if (typeof token === "number") {
        targets.push({ identity, version: token })
      } else if (token === null || token === undefined) {
        // An adopted row whose token was never written: assert exactly
        // that, and the successful write heals it.
        targets.push({ identity, version: "null" })
      } else {
        // A token a field policy withheld cannot be asserted; refusing
        // the row here is honest, guessing a version is not.
        unreadable.push({
          identity,
          status: "refused",
          code: "version_unreadable",
          message: "the record's version is not readable here",
          issues: [],
          notices: [],
          version: null,
        })
      }
    }
    return { targets, unreadable }
  }

  const warningRows = (report?.refusals ?? []).filter(
    (outcome) =>
      outcome.code === "validation_failed" &&
      outcome.issues.length > 0 &&
      outcome.issues.every((issue) => issue.severity === "warning"),
  )
  const warningMessages = [
    ...new Set(
      warningRows.flatMap((outcome) =>
        outcome.issues
          .filter((issue) => issue.severity === "warning")
          .map((issue) => issue.message),
      ),
    ),
  ]

  const apply = async (
    identities?: ReadonlySet<string>,
    acknowledge: string[] = [],
    carried?: MassUpdateReport,
  ) => {
    if (!field) {
      return
    }
    const { targets, unreadable } = buildTargets(identities)
    if (targets.length + unreadable.length === 0) {
      setError("None of the selected rows are loaded.")
      return
    }
    if (targets.length > massUpdate.limit) {
      setError(
        `Mass update accepts at most ${massUpdate.limit.toLocaleString()} rows per apply.`,
      )
      return
    }
    setApplying(true)
    setError(null)
    try {
      const changes = { [field.name]: clearField ? null : value }
      const result =
        targets.length > 0
          ? await api.massUpdate(massUpdate, changes, targets, acknowledge)
          : { outcomes: [], updated: 0, refused: 0 }
      const refusals = [
        ...(carried?.refusals.filter(
          (outcome) =>
            !result.outcomes.some(
              (answer) =>
                String(answer.identity) === String(outcome.identity),
            ),
        ) ?? []),
        ...result.outcomes.filter(
          (outcome) => outcome.status === "refused",
        ),
        ...unreadable,
      ]
      const updated = (carried?.updated ?? 0) + result.updated
      setReport({
        updated,
        total: carried?.total ?? targets.length + unreadable.length,
        refusals,
      })
      if (result.updated > 0) {
        onApplied()
      }
    } catch (caught) {
      setError(
        caught instanceof TideApiError
          ? caught.message
          : "The mass update could not be sent.",
      )
    } finally {
      setApplying(false)
    }
  }

  const applyAnyway = () => {
    if (!report) {
      return
    }
    const identities = new Set(
      warningRows.map((outcome) => String(outcome.identity)),
    )
    const rules = [
      ...new Set(
        warningRows.flatMap((outcome) =>
          outcome.issues
            .filter((issue) => issue.severity === "warning")
            .map((issue) => issue.rule),
        ),
      ),
    ]
    void apply(identities, rules, {
      updated: report.updated,
      total: report.total,
      refusals: report.refusals.filter(
        (outcome) => !identities.has(String(outcome.identity)),
      ),
    })
  }

  const valueMissing =
    !clearField &&
    field !== undefined &&
    field.field_type !== "boolean" &&
    (value === "" || value === null || value === undefined)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-[1px]"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tide-mass-update-title"
        tabIndex={-1}
        className="flex max-h-[min(38rem,calc(100vh-2rem))] w-full max-w-lg flex-col overflow-hidden rounded-2xl border bg-card shadow-2xl"
        onKeyDown={(event) => {
          event.stopPropagation()
          if (event.key === "Escape") {
            event.preventDefault()
            onClose()
            return
          }
          keepFocusInDialog(event)
        }}
      >
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4">
          <div>
            <h2 id="tide-mass-update-title" className="text-lg font-semibold">
              Mass update
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {selected.size === 1
                ? "One selected record."
                : `${selected.size.toLocaleString()} selected records.`}{" "}
              Every row answers for itself.
            </p>
          </div>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label="Close mass update"
            onClick={onClose}
          >
            <X />
          </Button>
        </header>

        <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">
          {report === null ? (
            <>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">Field</span>
                <Select
                  value={fieldName === "" ? undefined : fieldName}
                  onValueChange={(picked) => {
                    setFieldName(picked)
                    const next = fields.find(
                      (candidate) => candidate.name === picked,
                    )
                    setValue(next?.field_type === "boolean" ? false : "")
                    setClearField(false)
                  }}
                >
                  <SelectTrigger aria-label="Field to change">
                    <SelectValue placeholder="Choose a field…" />
                  </SelectTrigger>
                  <SelectContent>
                    {fields.map((candidate) => (
                      <SelectItem key={candidate.name} value={candidate.name}>
                        {candidate.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>

              {field ? (
                <div className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium">New value</span>
                  <GridCellEditor
                    column={field}
                    field={field}
                    value={clearField ? "" : value}
                    disabled={applying || clearField}
                    onChange={setValue}
                  />
                  {!field.required ? (
                    <label className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                      <Checkbox
                        aria-label="Clear the field"
                        checked={clearField}
                        onCheckedChange={(checked) =>
                          setClearField(checked === true)
                        }
                      />
                      {/* An untouched input must never quietly blank the
                          selection; blanking is this explicit choice. */}
                      Clear the field on every selected row
                    </label>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-sm font-medium">
                Updated {report.updated.toLocaleString()} of{" "}
                {report.total.toLocaleString()}{" "}
                {report.total === 1 ? "record" : "records"}.
              </p>
              {report.refusals.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {report.refusals.map((outcome) => (
                    <li
                      key={String(outcome.identity)}
                      data-testid="mass-update-refusal"
                      className="rounded-lg border border-border/70 bg-muted/40 px-3 py-2 text-xs"
                    >
                      <span className="font-medium">
                        {labelOf(outcome.identity)}
                      </span>{" "}
                      <span className="text-muted-foreground">
                        {outcome.message ?? outcome.code ?? "refused"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {warningMessages.length > 0 ? (
                <div
                  role="status"
                  className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-xs text-amber-800 dark:text-amber-300"
                >
                  <p className="font-medium">
                    {warningRows.length === 1
                      ? "One row was refused only by warnings."
                      : `${warningRows.length} rows were refused only by warnings.`}
                  </p>
                  <ul className="mt-1 list-disc pl-4">
                    {warningMessages.map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}

          {error ? (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-xl border border-destructive/25 bg-destructive/8 px-3 py-2.5 text-xs text-destructive"
            >
              <CircleAlert className="size-3.5 shrink-0" />
              {error}
            </div>
          ) : null}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t px-5 py-3.5">
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
          {report === null ? (
            <Button
              type="button"
              disabled={!field || applying || valueMissing}
              onClick={() => {
                void apply()
              }}
            >
              {applying ? "Applying…" : "Apply"}
            </Button>
          ) : warningRows.length > 0 ? (
            <Button
              type="button"
              variant="outline"
              className="border-amber-500/45 text-amber-700 hover:bg-amber-500/10 dark:text-amber-300"
              disabled={applying}
              onClick={applyAnyway}
            >
              {applying ? "Applying…" : "Apply anyway"}
            </Button>
          ) : null}
        </footer>
      </section>
    </div>
  )
}
