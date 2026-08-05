import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CirclePlus,
  LoaderCircle,
  Search,
  X,
} from "lucide-react"

import { TideDisplayValue } from "@/components/tide-display-value"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationFormField,
  TidePresentationFormGroup,
  TidePresentationLookup,
  TidePresentationManifest,
  TideRecord,
} from "@/lib/contracts"
import { useDialogFocus } from "@/lib/dialog-focus"
import {
  acceptsNumericDraft,
  formDraft,
  isEditableForm,
  mutationPayload,
  normalizeNumericDraft,
  shiftIsoDate,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import { cn } from "@/lib/utils"

interface RecordFormEditorProps {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  draft: TideFormDraft
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  idScope?: string
  onChange: (name: string, value: unknown) => void
  onApplyValues: (values: Record<string, unknown>) => void
}

export function RecordFormEditor({
  api,
  form,
  forms,
  draft,
  editableFields,
  errors,
  disabled,
  idScope,
  onChange,
  onApplyValues,
}: RecordFormEditorProps) {
  const record = draft as TideRecord
  return (
    <>
      {form.sections.map((section, index) =>
        section.kind === "group" ? (
          <EditorGroup
            key={`editor-${index}-${section.label}`}
            api={api}
            form={form}
            forms={forms}
            record={record}
            section={section}
            editableFields={editableFields}
            errors={errors}
            disabled={disabled}
            idScope={idScope}
            onChange={onChange}
            onApplyValues={onApplyValues}
          />
        ) : null,
      )}
    </>
  )
}

function EditorGroup({
  api,
  form,
  forms,
  record,
  section,
  editableFields,
  errors,
  disabled,
  idScope,
  onChange,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  record: TideRecord
  section: TidePresentationFormGroup
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  idScope?: string
  onChange: (name: string, value: unknown) => void
  onApplyValues: (values: Record<string, unknown>) => void
}) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">{section.label}</h2>
      <div className="space-y-3">
        {section.rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className="tide-form-row grid gap-3"
            style={
              {
                "--tide-form-columns": row.length,
              } as CSSProperties
            }
          >
            {row.map((name) => {
              const field = form.fields[name]
              const editable = editableFields.has(name)
              return (
                <div
                  key={name}
                  className={cn(
                    "min-w-0 rounded-xl border px-3.5 py-3",
                    editable
                      ? "bg-background"
                      : "border-border/75 bg-muted/35",
                    errors[name] &&
                      "border-destructive/60 ring-2 ring-destructive/10",
                  )}
                >
                  {editable ? (
                    <FieldEditor
                      api={api}
                      form={form}
                      forms={forms}
                      field={field}
                      value={record[name]}
                      draft={record}
                      error={errors[name]}
                      disabled={disabled}
                      idScope={idScope}
                      onChange={(value) => onChange(name, value)}
                      onApplyValues={onApplyValues}
                    />
                  ) : (
                    <>
                      <p className="mb-1.5 truncate text-xs font-medium text-muted-foreground">
                        {field.label}
                      </p>
                      <TideDisplayValue
                        api={api}
                        column={field}
                        record={record}
                        wrap
                        className="min-h-5 text-sm"
                      />
                    </>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

function FieldEditor({
  api,
  form,
  forms,
  field,
  value,
  draft,
  error,
  disabled,
  idScope,
  onChange,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  field: TidePresentationFormField
  value: unknown
  draft: TideFormDraft
  error?: string
  disabled: boolean
  idScope?: string
  onChange: (value: unknown) => void
  onApplyValues: (values: Record<string, unknown>) => void
}) {
  const id = formEditorId(form, field.name, idScope)
  const helpId = `${id}-help`
  const errorId = `${id}-error`
  const describedBy = error ? errorId : field.help ? helpId : undefined

  if (field.field_type === "reference" && field.lookup) {
    return (
      <ReferenceEditor
        api={api}
        form={form}
        forms={forms}
        field={field}
        lookup={field.lookup}
        value={value}
        draft={draft}
        error={error}
        disabled={disabled}
        idScope={idScope}
        onApplyValues={onApplyValues}
      />
    )
  }

  if (field.field_type === "boolean") {
    return (
      <div>
        <label
          htmlFor={id}
          className="flex min-h-9 cursor-pointer items-center gap-2.5 text-sm font-medium"
        >
          <input
            id={id}
            data-tide-editor
            type="checkbox"
            className="size-4 rounded border-border accent-primary"
            checked={Boolean(value)}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-describedby={describedBy}
            onChange={(event) => onChange(event.target.checked)}
            onKeyDown={moveOnEnter}
          />
          {field.label}
        </label>
        <FieldMessage
          field={field}
          error={error}
          helpId={helpId}
          errorId={errorId}
        />
      </div>
    )
  }

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block truncate text-xs font-medium text-muted-foreground"
      >
        {field.label}
        {field.required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {field.field_type === "choice" ? (
        <select
          id={id}
          data-tide-editor
          className={editorClass(error)}
          value={String(value ?? "")}
          disabled={disabled}
          required={field.required}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={moveOnEnter}
        >
          {!field.required ? <option value="">None</option> : null}
          {field.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice.replace(/[_-]+/g, " ")}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          data-tide-editor
          className={editorClass(error)}
          type={inputType(field)}
          inputMode={
            field.field_type === "decimal"
              ? "decimal"
              : field.field_type === "integer"
                ? "numeric"
                : undefined
          }
          value={String(value ?? "")}
          disabled={disabled}
          required={field.required}
          maxLength={field.max_length ?? undefined}
          pattern={field.regex ?? undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          onChange={(event) => {
            const next = event.target.value
            if (
              field.field_type !== "decimal" &&
              field.field_type !== "integer"
            ) {
              onChange(next)
            } else if (acceptsNumericDraft(field, next)) {
              onChange(next)
            }
          }}
          onBlur={(event) => {
            if (
              field.field_type === "decimal" ||
              field.field_type === "integer"
            ) {
              onChange(normalizeNumericDraft(field, event.target.value))
            }
          }}
          onKeyDown={(event) => {
            if (
              field.field_type === "date" &&
              (event.key === "+" || event.key === "-")
            ) {
              event.preventDefault()
              const current =
                String(value ?? "") ||
                new Date().toISOString().slice(0, 10)
              onChange(shiftIsoDate(current, event.key === "+" ? 1 : -1))
              return
            }
            moveOnEnter(event)
          }}
        />
      )}
      <FieldMessage
        field={field}
        error={error}
        helpId={helpId}
        errorId={errorId}
      />
    </div>
  )
}

function ReferenceEditor({
  api,
  form,
  forms,
  field,
  lookup,
  value,
  draft,
  error,
  disabled,
  idScope,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  field: TidePresentationFormField
  lookup: TidePresentationLookup
  value: unknown
  draft: TideFormDraft
  error?: string
  disabled: boolean
  idScope?: string
  onApplyValues: (values: Record<string, unknown>) => void
}) {
  const [open, setOpen] = useState(false)
  const id = formEditorId(form, field.name, idScope)
  const helpId = `${id}-help`
  const errorId = `${id}-error`
  const describedBy = error ? errorId : field.help ? helpId : undefined

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block truncate text-xs font-medium text-muted-foreground"
      >
        {field.label}
        {field.required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <div className="flex min-w-0 gap-2">
        <div
          className={cn(
            "flex h-9 min-w-0 flex-1 items-center rounded-md border bg-background px-3 text-sm",
            error && "border-destructive/65",
          )}
          aria-live="polite"
        >
          <TideDisplayValue
            api={api}
            column={field}
            record={{ [field.name]: value }}
            className="truncate"
          />
        </div>
        <Button
          id={id}
          data-tide-editor
          type="button"
          variant="outline"
          disabled={disabled}
          aria-label={`Select ${field.label}`}
          aria-describedby={describedBy}
          aria-invalid={Boolean(error)}
          onClick={() => setOpen(true)}
        >
          Select…
        </Button>
      </div>
      <FieldMessage
        field={field}
        error={error}
        helpId={helpId}
        errorId={errorId}
      />
      {open ? (
        <ReferenceLookupDialog
          api={api}
          form={form}
          forms={forms}
          lookup={lookup}
          draft={draft}
          onClose={() => setOpen(false)}
          onSelected={(values) => {
            onApplyValues(values)
            setOpen(false)
          }}
        />
      ) : null}
    </div>
  )
}

function ReferenceLookupDialog({
  api,
  form,
  forms,
  lookup,
  draft,
  onClose,
  onSelected,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  lookup: TidePresentationLookup
  draft: TideFormDraft
  onClose: () => void
  onSelected: (values: Record<string, unknown>) => void
}) {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLElement>(null)
  const keepFocusInDialog = useDialogFocus(dialogRef)
  const searchRef = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search.trim(), 250)
  const [selectedIdentity, setSelectedIdentity] = useState<unknown | null>(
    null,
  )
  const [creating, setCreating] = useState(false)
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const createForm =
    lookup.create_view === null ? null : forms[lookup.create_view] ?? null
  const createAvailable = Boolean(
    createForm &&
      lookup.operations.includes("create") &&
      isEditableForm(createForm),
  )
  const query = useQuery({
    queryKey: [
      "reference-lookup",
      lookup.owner_entity,
      lookup.field,
      debouncedSearch,
    ],
    queryFn: ({ signal }) =>
      api.searchLookup(lookup, debouncedSearch, signal),
    enabled: !creating,
    staleTime: 10_000,
    // The search term is part of the key, so without this the table empties
    // every time the debounce fires and fills again when the results land.
    // That flickers, and it quietly drops a choice: `selected` is looked up in
    // the rows currently held, so a row picked from the list as it stood --
    // which is the only list the person could see -- stops being selectable
    // the moment the narrower results arrive, and the Select button goes dead
    // under the pointer.
    placeholderData: (previous) => previous,
  })
  const records = query.data ?? []
  const selected = records.find(
    (record) =>
      selectedIdentity !== null &&
      String(record[lookup.identity_field]) === String(selectedIdentity),
  )
  const selectMutation = useMutation({
    mutationFn: async (record: TideRecord) => {
      const identity = record[lookup.identity_field]
      if (identity === null || identity === undefined) {
        throw new TideApiError("The selected record has no identity.")
      }
      const values = await api.applyReferenceSelection(
        lookup,
        referenceSelectionDraft(form, draft),
        identity,
      )
      return { identity, record, values }
    },
    onSuccess: ({ identity, record, values }) => {
      if (form.fields[lookup.field]?.reference) {
        queryClient.setQueryData(
          [
            "reference-display",
            form.fields[lookup.field].reference?.entity,
            identity,
          ],
          record,
        )
      }
      onSelected(values)
    },
    onError: (error) => {
      setSelectionError(
        error instanceof TideApiError
          ? error.message
          : "The selected record could not be applied.",
      )
    },
  })

  useEffect(() => {
    requestAnimationFrame(() => {
      if (creating) {
        dialogRef.current
          ?.querySelector<HTMLElement>("[data-tide-editor]:not(:disabled)")
          ?.focus()
      } else {
        searchRef.current?.focus()
      }
    })
  }, [creating])

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
        aria-labelledby="tide-lookup-title"
        tabIndex={-1}
        className="flex max-h-[min(46rem,calc(100vh-2rem))] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border bg-card shadow-2xl"
        onKeyDown={(event) => {
          event.stopPropagation()
          if (event.key === "Escape") {
            event.preventDefault()
            if (creating) {
              setCreating(false)
            } else {
              onClose()
            }
            return
          }
          keepFocusInDialog(event)
        }}
      >
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4">
          <div>
            <h2 id="tide-lookup-title" className="text-lg font-semibold">
              {creating && createForm
                ? `New ${createForm.label}`
                : lookup.title}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {creating
                ? "The parent draft remains open while this related record is created."
                : `Search ${lookup.search_fields.join(", ")} and choose one record.`}
            </p>
          </div>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label="Close lookup"
            onClick={onClose}
          >
            <X />
          </Button>
        </header>

        {creating && createForm ? (
          <NestedCreate
            api={api}
            form={createForm}
            forms={forms}
            parentForm={form}
            lookup={lookup}
            parentDraft={draft}
            onCancel={() => setCreating(false)}
            onSelected={(record, values) => {
              const identity = record[lookup.identity_field]
              queryClient.setQueryData(
                [
                  "reference-display",
                  form.fields[lookup.field].reference?.entity,
                  identity,
                ],
                record,
              )
              onSelected(values)
            }}
          />
        ) : (
          <>
            <div className="border-b p-4">
              <div className="relative">
                <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" />
                <Input
                  ref={searchRef}
                  value={search}
                  className="pr-9 pl-9"
                  aria-label="Search lookup records"
                  placeholder={`Search ${lookup.search_fields.join(", ")}`}
                  onChange={(event) => {
                    setSearch(event.target.value)
                    setSelectedIdentity(null)
                    setSelectionError(null)
                  }}
                />
                {query.isFetching ? (
                  <LoaderCircle className="absolute top-2.5 right-3 size-4 animate-spin text-muted-foreground" />
                ) : null}
              </div>
            </div>
            <div className="min-h-64 flex-1 overflow-auto">
              {query.isPending ? (
                <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
                  <LoaderCircle className="size-4 animate-spin" />
                  Loading records…
                </div>
              ) : query.error ? (
                <div className="flex min-h-64 items-center justify-center p-6 text-sm text-destructive">
                  The lookup records could not be loaded.
                </div>
              ) : records.length === 0 ? (
                <div className="flex min-h-64 items-center justify-center p-6 text-sm text-muted-foreground">
                  No matching records
                </div>
              ) : (
                <table className="w-full min-w-max border-collapse text-sm">
                  <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
                    <tr>
                      {lookup.columns.map((column) => (
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
                    {records.map((record) => {
                      const identity = record[lookup.identity_field]
                      const active =
                        selectedIdentity !== null &&
                        String(identity) === String(selectedIdentity)
                      return (
                        <tr
                          key={String(identity)}
                          tabIndex={0}
                          aria-selected={active}
                          className={cn(
                            "cursor-pointer border-b outline-none last:border-b-0 hover:bg-accent/45 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/30",
                            active && "bg-primary/10",
                          )}
                          onClick={() => setSelectedIdentity(identity)}
                          onDoubleClick={() =>
                            selectMutation.mutate(record)
                          }
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault()
                              selectMutation.mutate(record)
                            }
                          }}
                        >
                          {lookup.columns.map((column) => (
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
                                record={record}
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
            <footer className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
              <div className="flex items-center gap-3">
                {createAvailable ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setSelectionError(null)
                      setCreating(true)
                    }}
                  >
                    <CirclePlus />
                    New {createForm?.label}
                  </Button>
                ) : null}
                <span className="text-xs text-muted-foreground">
                  {records.length} loaded
                </span>
              </div>
              <div className="flex items-center gap-2">
                {selectionError ? (
                  <span role="alert" className="text-xs text-destructive">
                    {selectionError}
                  </span>
                ) : null}
                <Button type="button" variant="outline" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  disabled={!selected || selectMutation.isPending}
                  onClick={() => {
                    if (selected) {
                      selectMutation.mutate(selected)
                    }
                  }}
                >
                  {selectMutation.isPending ? (
                    <LoaderCircle className="animate-spin" />
                  ) : null}
                  Select
                </Button>
              </div>
            </footer>
          </>
        )}
      </section>
    </div>
  )
}

function NestedCreate({
  api,
  form,
  forms,
  parentForm,
  lookup,
  parentDraft,
  onCancel,
  onSelected,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  parentForm: TideFormPresentation
  lookup: TidePresentationLookup
  parentDraft: TideFormDraft
  onCancel: () => void
  onSelected: (
    record: TideRecord,
    values: Record<string, unknown>,
  ) => void
}) {
  const [draft, setDraft] = useState<TideFormDraft>(() => formDraft(form))
  const [errors, setErrors] = useState<TideFormErrors>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  const editableFields = new Set(
    Object.values(form.fields)
      .filter((field) => field.writable)
      .map((field) => field.name),
  )
  const saveMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const saved = await api.createLookupRecord(lookup, payload)
      const identity = saved.record[lookup.identity_field]
      if (identity === null || identity === undefined) {
        throw new TideApiError("The created record has no identity.")
      }
      const values = await api.applyReferenceSelection(
        lookup,
        referenceSelectionDraft(parentForm, parentDraft),
        identity,
      )
      return { record: saved.record, values }
    },
    onSuccess: ({ record, values }) => onSelected(record, values),
    onError: (error) => {
      if (error instanceof TideApiError) {
        setSaveError(error.message)
        const next: TideFormErrors = {}
        for (const issue of error.issues) {
          for (const name of issue.fields) {
            if (form.fields[name] && !next[name]) {
              next[name] = issue.message
            }
          }
        }
        setErrors(next)
      } else {
        setSaveError("The related record could not be created.")
      }
    },
  })

  function saveAndSelect() {
    const nextErrors = validateFormDraft(form, draft, editableFields)
    setErrors(nextErrors)
    setSaveError(null)
    if (Object.keys(nextErrors).length > 0) {
      const first = Object.keys(nextErrors)[0]
      requestAnimationFrame(() =>
        document.getElementById(formEditorId(form, first))?.focus(),
      )
      return
    }
    saveMutation.mutate(
      mutationPayload(form, draft, editableFields),
    )
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        {saveError ? (
          <p
            role="alert"
            className="mb-4 rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
          >
            {saveError}
          </p>
        ) : null}
        <RecordFormEditor
          api={api}
          form={form}
          forms={forms}
          draft={draft}
          editableFields={editableFields}
          errors={errors}
          disabled={saveMutation.isPending}
          onChange={(name, value) => {
            setDraft((current) => ({ ...current, [name]: value }))
            setErrors((current) => {
              if (!current[name]) {
                return current
              }
              const next = { ...current }
              delete next[name]
              return next
            })
          }}
          onApplyValues={(values) =>
            setDraft((current) => ({ ...current, ...values }))
          }
        />
      </div>
      <footer className="flex justify-end gap-2 border-t px-4 py-3">
        <Button
          type="button"
          variant="outline"
          disabled={saveMutation.isPending}
          onClick={onCancel}
        >
          Back
        </Button>
        <Button
          type="button"
          disabled={saveMutation.isPending}
          onClick={saveAndSelect}
        >
          {saveMutation.isPending ? (
            <LoaderCircle className="animate-spin" />
          ) : null}
          Save &amp; Select
        </Button>
      </footer>
    </>
  )
}

function referenceSelectionDraft(
  form: TideFormPresentation,
  draft: TideFormDraft,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.values(form.fields)
      .filter((field) => field.writable)
      .map((field) => [field.name, draft[field.name]])
      .filter(
        ([, value]) =>
          value !== "" && value !== null && value !== undefined,
      ),
  )
}

export function formEditorId(
  form: TideFormPresentation,
  fieldName: string,
  scope?: string,
): string {
  const view = `${form.view}${scope ? `-${scope}` : ""}`.replace(
    /[^A-Za-z0-9_-]+/g,
    "-",
  )
  return `tide-editor-${view}-${fieldName}`
}

function FieldMessage({
  field,
  error,
  helpId,
  errorId,
}: {
  field: TidePresentationFormField
  error?: string
  helpId: string
  errorId: string
}) {
  if (error) {
    return (
      <p id={errorId} className="mt-1.5 text-xs text-destructive">
        {error}
      </p>
    )
  }
  return field.help ? (
    <p id={helpId} className="mt-1.5 text-xs text-muted-foreground">
      {field.help}
    </p>
  ) : null
}

function editorClass(error?: string): string {
  return cn(
    "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none transition-shadow placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-60",
    error && "border-destructive/65 focus:border-destructive",
  )
}

function inputType(field: TidePresentationFormField): string {
  if (field.field_type === "date") {
    return "date"
  }
  if (field.field_type === "datetime") {
    return "datetime-local"
  }
  return "text"
}

function moveOnEnter(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "Enter") {
    return
  }
  event.preventDefault()
  const editors = Array.from(
    document.querySelectorAll<HTMLElement>("[data-tide-editor]:not(:disabled)"),
  )
  const index = editors.indexOf(event.currentTarget)
  editors[index + 1]?.focus()
}
