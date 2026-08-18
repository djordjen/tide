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
  Ellipsis,
  LoaderCircle,
  Search,
  SquareArrowOutUpRight,
  X,
} from "lucide-react"

import { TideDisplayValue } from "@/components/tide-display-value"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
  asEmphasis,
  textEmphasisClass,
  type TideEmphasis,
} from "@/lib/emphasis"
import { referenceRecordHref } from "@/lib/reference-link"
import {
  acceptsNumericDraft,
  formDraft,
  isEditableForm,
  mutationPayload,
  normalizeNumericDraft,
  referenceSelectionDraft,
  shiftIsoDate,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import { cn } from "@/lib/utils"
import {
  fieldCellClass,
  fieldGroupClass,
  fieldLabelClass,
  readOnlyValueClass,
  sectionCaptionClass,
} from "./form-field"

interface RecordFormEditorProps {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  /** Capability-filtered browse views, for the reference controls' doors. */
  views?: TidePresentationManifest["views"]
  draft: TideFormDraft
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  idScope?: string
  /** The entity's appearance verdict for this record, field by field. */
  appearance?: Record<string, string>
  /** Fields an appearance rule hides on this record. */
  hidden?: string[]
  /** Appended to the first group's heading, e.g. "· row 2 of 3". */
  headingSuffix?: string
  onChange: (name: string, value: unknown) => void
  onApplyValues: (values: Record<string, unknown>) => void
}

export function RecordFormEditor({
  api,
  form,
  forms,
  views,
  draft,
  editableFields,
  errors,
  disabled,
  idScope,
  appearance,
  hidden,
  headingSuffix,
  onChange,
  onApplyValues,
}: RecordFormEditorProps) {
  const record = draft as TideRecord
  const firstGroup = form.sections.findIndex(
    (section) => section.kind === "group",
  )
  return (
    <>
      {form.sections.map((section, index) =>
        section.kind === "group" ? (
          <EditorGroup
            key={`editor-${index}-${section.label}`}
            api={api}
            form={form}
            forms={forms}
            views={views}
            record={record}
            section={section}
            editableFields={editableFields}
            errors={errors}
            disabled={disabled}
            idScope={idScope}
            appearance={appearance}
            hidden={hidden}
            headingSuffix={
              index === firstGroup ? headingSuffix : undefined
            }
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
  views,
  record,
  section,
  editableFields,
  errors,
  disabled,
  idScope,
  appearance,
  hidden,
  headingSuffix,
  onChange,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  views?: TidePresentationManifest["views"]
  record: TideRecord
  section: TidePresentationFormGroup
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  idScope?: string
  appearance?: Record<string, string>
  hidden?: string[]
  headingSuffix?: string
  onChange: (name: string, value: unknown) => void
  onApplyValues: (values: Record<string, unknown>) => void
}) {
  return (
    <section>
      <h2 className={sectionCaptionClass}>
        {section.label}
        {headingSuffix ? (
          <span className="font-normal text-muted-foreground">
            {headingSuffix}
          </span>
        ) : null}
      </h2>
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
              // A rule hid this field on this record. Presentation only: the
              // value arrived and the server would still accept a write --
              // withholding one is a permission's job, not a rule's.
              if (hidden?.includes(name)) {
                return null
              }
              const field = form.fields[name]
              const editable = editableFields.has(name)
              // The label carries a field's verdict rather than the control:
              // an input already speaks in colour for its own error and
              // disabled states, and a second meaning in the same place is
              // one a reader has to disambiguate.
              const emphasis = asEmphasis(appearance?.[name])
              return (
                <div
                  key={name}
                  className={cn(fieldCellClass, !editable && fieldGroupClass)}
                >
                  {editable ? (
                    <FieldEditor
                      api={api}
                      form={form}
                      forms={forms}
                      views={views}
                      field={field}
                      value={record[name]}
                      draft={record}
                      error={errors[name]}
                      disabled={disabled}
                      idScope={idScope}
                      emphasis={emphasis}
                      onChange={(value) => onChange(name, value)}
                      onApplyValues={onApplyValues}
                    />
                  ) : (
                    <>
                      <p
                        className={cn(
                          fieldLabelClass,
                          textEmphasisClass(emphasis),
                        )}
                        data-emphasis={emphasis}
                      >
                        {field.label}
                      </p>
                      <TideDisplayValue
                        api={api}
                        column={field}
                        record={record}
                        views={views}
                        wrap
                        className={readOnlyValueClass}
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
  views,
  field,
  value,
  draft,
  error,
  disabled,
  idScope,
  emphasis,
  onChange,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  views?: TidePresentationManifest["views"]
  field: TidePresentationFormField
  value: unknown
  draft: TideFormDraft
  error?: string
  disabled: boolean
  idScope?: string
  emphasis?: TideEmphasis
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
        views={views}
        field={field}
        lookup={field.lookup}
        value={value}
        draft={draft}
        error={error}
        disabled={disabled}
        idScope={idScope}
        emphasis={emphasis}
        onApplyValues={onApplyValues}
      />
    )
  }

  if (field.field_type === "boolean") {
    return (
      <div className={fieldGroupClass}>
        {/* The box belongs in the value column with every other control, so
            the label is its own element rather than text beside the input. */}
        <label
          htmlFor={id}
          className={cn(fieldLabelClass, textEmphasisClass(emphasis))}
          data-emphasis={emphasis}
        >
          {field.label}
        </label>
        <div className="flex min-h-9 items-center">
          {/* The one editor still drawn from a bare element. `ui/input.tsx`
              is text-shaped -- full width, 36px tall, padded for a caret --
              and a tick box is none of those; shadcn draws this from a
              separate Radix primitive that is not vendored here. A native
              checkbox is also the one control a screen reader and a phone
              both already handle perfectly, so it is left until a Checkbox
              is wanted for its own sake rather than for the tally. */}
          <input
            id={id}
            data-tide-editor
            type="checkbox"
            className="size-4 cursor-pointer rounded border-border accent-primary"
            checked={Boolean(value)}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-describedby={describedBy}
            onChange={(event) => onChange(event.target.checked)}
            onKeyDown={moveOnEnter}
          />
        </div>
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
    <div className={fieldGroupClass}>
      <label
        htmlFor={id}
        className={cn(fieldLabelClass, textEmphasisClass(emphasis))}
        data-emphasis={emphasis}
      >
        {field.label}
        {field.required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {field.values?.length ? (
        // A captioned field keeps its stored type. An option value is a
        // string, as every option value in a listbox is, so the picked one is
        // looked up rather than passed on -- handing the server "2" for an
        // integer column would be a different value from the 2 it declared.
        <PickerEditor
          id={id}
          label={field.label}
          value={value}
          options={field.values.map((item) => ({
            key: String(item.value),
            label: item.label,
            pick: () => onChange(item.value),
          }))}
          required={field.required}
          disabled={disabled}
          error={error}
          describedBy={describedBy}
          onClear={() => onChange(null)}
        />
      ) : field.field_type === "choice" ? (
        <PickerEditor
          id={id}
          label={field.label}
          value={value}
          options={field.choices.map((choice) => ({
            key: choice,
            label: choice.replace(/[_-]+/g, " "),
            pick: () => onChange(choice),
          }))}
          required={field.required}
          disabled={disabled}
          error={error}
          describedBy={describedBy}
          onClear={() => onChange(null)}
        />
      ) : (
        <Input
          id={id}
          data-tide-editor
          className={errorClass(error)}
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
  views,
  field,
  lookup,
  value,
  draft,
  error,
  disabled,
  idScope,
  emphasis,
  onApplyValues,
}: {
  api: TideApi
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  views?: TidePresentationManifest["views"]
  field: TidePresentationFormField
  lookup: TidePresentationLookup
  value: unknown
  draft: TideFormDraft
  error?: string
  disabled: boolean
  idScope?: string
  emphasis?: TideEmphasis
  onApplyValues: (values: Record<string, unknown>) => void
}) {
  const [open, setOpen] = useState(false)
  const id = formEditorId(form, field.name, idScope)
  const helpId = `${id}-help`
  const errorId = `${id}-error`
  const describedBy = error ? errorId : field.help ? helpId : undefined
  const recordHref = referenceRecordHref(views, field.reference, value)
  // Clearing is offered only where empty is a legal value: emptying a
  // required reference could only manufacture the service's refusal.
  const clearable =
    !field.required && value !== null && value !== undefined && !disabled

  return (
    <div className={fieldGroupClass}>
      <label
        htmlFor={id}
        className={cn(fieldLabelClass, textEmphasisClass(emphasis))}
        data-emphasis={emphasis}
      >
        {field.label}
        {field.required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <div
        className={cn(
          // One combobox-shaped well: the chosen value with its picker on
          // the trailing edge, the way a select carries its chevron. The
          // well is the same dress as `ui/input.tsx`; only the embedded
          // button is clickable.
          "flex h-9 min-w-0 items-center gap-1 rounded-lg border border-input bg-muted/55 pr-1 pl-3 text-sm shadow-xs dark:bg-input/40",
          error && "border-destructive/65",
        )}
        aria-live="polite"
      >
        <TideDisplayValue
          api={api}
          column={field}
          record={{ [field.name]: value }}
          className="min-w-0 flex-1 truncate"
        />
        {clearable ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={`Clear ${field.label}`}
            title={`Clear ${field.label}`}
            onClick={() => onApplyValues({ [field.name]: null })}
          >
            <X />
          </Button>
        ) : null}
        {recordHref ? (
          // A door to the record this value names -- in a new tab, so an
          // open draft can never be lost to a side trip.
          <Button
            asChild
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
          >
            <a
              href={recordHref}
              target="_blank"
              rel="noopener"
              aria-label={`Open ${field.label}`}
              title={`Open ${field.label}`}
            >
              <SquareArrowOutUpRight />
            </a>
          </Button>
        ) : null}
        <Button
          id={id}
          data-tide-editor
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
          disabled={disabled}
          aria-label={`Select ${field.label}`}
          title={`Select ${field.label}`}
          aria-describedby={describedBy}
          aria-invalid={Boolean(error)}
          onClick={() => setOpen(true)}
        >
          <Ellipsis />
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

/**
 * What an editor in error looks like -- and nothing else.
 *
 * This used to restate the whole control surface, a second copy of what
 * `ui/input.tsx` already carries, and the two had drifted: `rounded-md`
 * against `rounded-lg`, `focus` against `focus-visible`, one with a shadow
 * and one without. Every editor now wears the code-owned surface and adds
 * only this.
 */
function errorClass(error?: string): string {
  return cn(error && "border-destructive/65 focus-visible:border-destructive")
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

/**
 * One picker for both kinds of closed set: `choice` members and `values` codes.
 *
 * They differ only in what a chosen option is worth -- a string literal for
 * one, the declared code with its own type for the other -- so each option
 * carries the assignment rather than the component deciding by field type.
 *
 * Two contracts the rest of the form relies on are kept by hand here, because
 * a listbox is a button and a popup rather than an element the browser drives:
 *
 * `data-tide-editor` marks it as a stop in the Enter traversal and as a
 * control the density journey measures, so it goes on the trigger -- the part
 * that occupies the row.
 *
 * Enter is claimed by the listbox while it is open, which is right: it picks.
 * Moving to the next field on Enter therefore only applies while it is closed,
 * and is done on the trigger's own handler rather than through `moveOnEnter`,
 * which would fight the popup for the key.
 */
function PickerEditor({
  id,
  label,
  value,
  options,
  required,
  disabled,
  error,
  describedBy,
  onClear,
}: {
  id: string
  label: string
  value: unknown
  options: readonly { key: string; label: string; pick: () => void }[]
  required: boolean
  disabled: boolean
  error?: string
  describedBy?: string
  onClear: () => void
}) {
  const [open, setOpen] = useState(false)
  const selected = value === null || value === undefined ? "" : String(value)
  const NONE = "__tide_none__"
  return (
    <Select
      open={open}
      onOpenChange={setOpen}
      value={selected === "" ? undefined : selected}
      disabled={disabled}
      onValueChange={(picked) => {
        if (picked === NONE) {
          onClear()
          return
        }
        options.find((option) => option.key === picked)?.pick()
      }}
    >
      <SelectTrigger
        id={id}
        data-tide-editor
        aria-label={label}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy}
        className={cn(error && "border-destructive/65")}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !open) {
            moveOnEnter(event)
          }
        }}
      >
        <SelectValue placeholder={required ? "Select…" : "None"} />
      </SelectTrigger>
      <SelectContent>
        {!required ? <SelectItem value={NONE}>None</SelectItem> : null}
        {options.map((option) => (
          <SelectItem key={option.key} value={option.key}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
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
