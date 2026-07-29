import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react"
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  LockKeyhole,
  Rows3,
  Save,
  ShieldCheck,
  X,
} from "lucide-react"

import { EditableCollection } from "@/components/editable-collection"
import {
  formEditorId,
  RecordFormEditor,
} from "@/components/record-form-editor"
import { TideDisplayValue } from "@/components/tide-display-value"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  TideApiError,
  type TideApi,
  type TideValidationIssue,
} from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormGroup,
  TidePresentationManifest,
  TidePresentationFormSection,
  TideRecord,
} from "@/lib/contracts"
import { formatRecordDisplay } from "@/lib/format"
import {
  changedMutationPayload,
  collectionDraftRows,
  collectionMutationPayload,
  collectionPayloadChanged,
  formDraft,
  isEditableForm,
  mutationPayload,
  validateCollectionDrafts,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import { cn } from "@/lib/utils"

interface RecordDetailProps {
  api: TideApi
  view: TideBrowsePresentation
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  mode: "create" | "update"
  identity: unknown | null
  position: number
  loadedCount: number
  canPrevious: boolean
  canNext: boolean
  navigationPending: boolean
  onPrevious: () => void
  onNext: () => void
  onClose: () => void
  onSaved: (record: TideRecord, mode: "create" | "update") => void
}

export function RecordDetail({
  api,
  view,
  form,
  forms,
  mode,
  identity,
  position,
  loadedCount,
  canPrevious,
  canNext,
  navigationPending,
  onPrevious,
  onNext,
  onClose,
  onSaved,
}: RecordDetailProps) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ["record-detail", view.view, identity],
    queryFn: ({ signal }) => {
      if (identity === null) {
        throw new Error("record identity missing")
      }
      return api.getRecord(view, identity, signal)
    },
    enabled: mode === "update" && identity !== null,
    staleTime: 15_000,
    placeholderData: keepPreviousData,
  })
  const tabs = useMemo(() => formTabs(form.sections), [form.sections])
  const [selectedTab, setSelectedTab] = useState(tabs[0] ?? "")
  const [draft, setDraft] = useState<TideFormDraft>(() =>
    formDraft(form),
  )
  const [collectionDrafts, setCollectionDrafts] = useState<
    Record<string, TideRecord[]>
  >(() => collectionDraftState(form))
  const [collectionErrors, setCollectionErrors] = useState<
    Record<string, TideFormErrors[]>
  >({})
  const [fieldErrors, setFieldErrors] = useState<TideFormErrors>({})
  const [saveError, setSaveError] = useState<TideApiError | null>(null)
  const snapshot = query.data
  const record = snapshot?.record
  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The record could not be loaded.")
        : null
  const editableForm = isEditableForm(form)
  const operationAvailable = view.operations.includes(
    mode === "create" ? "create" : "update",
  )
  const editableFields = useMemo(
    () =>
      new Set(
        Object.values(form.fields)
          .filter(
            (field) =>
              editableForm &&
              operationAvailable &&
              field.writable &&
              (mode === "create" ||
                (record?._tide?.writable_fields ?? []).includes(
                  field.name,
                )),
          )
          .map((field) => field.name),
      ),
    [
      editableForm,
      form.fields,
      mode,
      operationAvailable,
      record?._tide?.writable_fields,
    ],
  )
  const collectionSections = useMemo(
    () =>
      form.sections.filter(
        (
          section,
        ): section is TidePresentationFormCollection =>
          section.kind === "collection",
      ),
    [form.sections],
  )
  const editableCollections = useMemo(
    () =>
      new Set(
        collectionSections
          .filter(
            (section) =>
              operationAvailable &&
              section.writable === true &&
              (section.draft_operations ?? []).includes(
                mode === "create" ? "create" : "update",
              ) &&
              (mode === "create" ||
                (record?._tide?.writable_fields ?? []).includes(
                  section.name,
                )),
          )
          .map((section) => section.name),
      ),
    [
      collectionSections,
      mode,
      operationAvailable,
      record?._tide?.writable_fields,
    ],
  )
  const editorActive =
    editableFields.size > 0 || editableCollections.size > 0
  const scalarChanges =
    mode === "update" && record
      ? changedMutationPayload(form, draft, editableFields, record)
      : {}
  const collectionChanges =
    mode === "update" && record
      ? Object.fromEntries(
          collectionSections
            .filter(
              (section) =>
                editableCollections.has(section.name) &&
                collectionPayloadChanged(
                  section,
                  collectionDrafts[section.name] ?? [],
                  record[section.name],
                ),
            )
            .map((section) => [
              section.name,
              collectionMutationPayload(
                section,
                collectionDrafts[section.name] ?? [],
              ),
            ]),
        )
      : {}
  const changes = { ...scalarChanges, ...collectionChanges }
  const dirty = mode === "create" || Object.keys(changes).length > 0

  useEffect(() => {
    if (!tabs.includes(selectedTab)) {
      setSelectedTab(tabs[0] ?? "")
    }
  }, [selectedTab, tabs])

  useEffect(() => {
    if (mode === "create") {
      setDraft(formDraft(form))
      setCollectionDrafts(collectionDraftState(form))
      setCollectionErrors({})
      setFieldErrors({})
      setSaveError(null)
    } else if (record && !query.isPlaceholderData) {
      setDraft(formDraft(form, record))
      setCollectionDrafts(collectionDraftState(form, record))
      setCollectionErrors({})
      setFieldErrors({})
      setSaveError(null)
    }
  }, [form, mode, query.isPlaceholderData, record])

  const saveMutation = useMutation({
    mutationFn: ({
      payload,
    }: {
      payload: Record<string, unknown>
    }) =>
      mode === "create"
        ? api.createRecord(view, payload)
        : api.updateRecord(
            view,
            identity,
            payload,
            snapshot?.etag ?? null,
          ),
    onSuccess: (saved) => {
      setFieldErrors({})
      setSaveError(null)
      if (mode === "update" && identity !== null) {
        queryClient.setQueryData(
          ["record-detail", view.view, identity],
          saved,
        )
        setDraft(formDraft(form, saved.record))
        setCollectionDrafts(collectionDraftState(form, saved.record))
      }
      onSaved(saved.record, mode)
    },
    onError: (mutationError) => {
      const apiError =
        mutationError instanceof TideApiError
          ? mutationError
          : new TideApiError("The record could not be saved.")
      setSaveError(apiError)
      setFieldErrors(issueFieldErrors(form, apiError.issues))
    },
  })

  function save() {
    const clientErrors = validateFormDraft(
      form,
      draft,
      editableFields,
    )
    const nextCollectionErrors = Object.fromEntries(
      collectionSections
        .filter((section) => editableCollections.has(section.name))
        .map((section) => [
          section.name,
          validateCollectionDrafts(
            section,
            collectionDrafts[section.name] ?? [],
          ),
        ]),
    )
    setFieldErrors(clientErrors)
    setCollectionErrors(nextCollectionErrors)
    setSaveError(null)
    if (Object.keys(clientErrors).length > 0) {
      focusFirstError(form, clientErrors)
      return
    }
    const invalidCollection = collectionSections.find((section) =>
      (nextCollectionErrors[section.name] ?? []).some(
        (errors) => Object.keys(errors).length > 0,
      ),
    )
    if (invalidCollection) {
      focusFirstCollectionError(invalidCollection.name)
      return
    }
    const payload =
      mode === "create"
        ? {
            ...mutationPayload(form, draft, editableFields),
            ...Object.fromEntries(
              collectionSections
                .filter((section) =>
                  editableCollections.has(section.name),
                )
                .map((section) => [
                  section.name,
                  collectionMutationPayload(
                    section,
                    collectionDrafts[section.name] ?? [],
                  ),
                ]),
            ),
          }
        : changes
    if (mode === "update" && Object.keys(payload).length === 0) {
      return
    }
    saveMutation.mutate({ payload })
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === "PageUp" &&
        canPrevious &&
        !navigationPending &&
        !dirty
      ) {
        event.preventDefault()
        onPrevious()
      } else if (
        event.key === "PageDown" &&
        canNext &&
        !navigationPending &&
        !dirty
      ) {
        event.preventDefault()
        onNext()
      } else if (event.key === "Escape" && !saveMutation.isPending) {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [
    canNext,
    canPrevious,
    dirty,
    navigationPending,
    onClose,
    onNext,
    onPrevious,
    saveMutation.isPending,
  ])

  const visibleSections =
    tabs.length > 0
      ? form.sections.filter(
          (section) => (section.tab ?? "General") === selectedTab,
        )
      : form.sections
  const display = record
    ? formatRecordDisplay(
        form.display_template,
        record,
        view.identity_field,
      )
    : mode === "create"
      ? `New ${form.label}`
      : String(identity)
  const writable = new Set(record?._tide?.writable_fields ?? [])

  return (
    <main className="flex min-h-0 flex-1 flex-col p-4 md:p-6">
      <header className="mb-4 flex shrink-0 items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {mode === "create"
                ? `New ${form.label}`
                : `${form.label} — ${display || String(identity)}`}
            </h1>
            <Badge variant="outline">
              {mode === "create"
                ? "New record"
                : editorActive
                  ? "Secured editor"
                  : "Secured detail"}
            </Badge>
            {query.isFetching ? (
              <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
            ) : null}
          </div>
          <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            {mode === "create"
              ? "Defaults and validation come from the compiled application model"
              : `Record ${position + 1} of ${loadedCount} loaded in the current query`}
          </p>
        </div>
        <Button
          aria-label="Close record"
          className="shrink-0 md:hidden"
          size="icon"
          variant="ghost"
          onClick={onClose}
        >
          <X />
        </Button>
      </header>

      {error ? (
        <div
          role="alert"
          className="mb-4 flex shrink-0 items-center justify-between gap-4 rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
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
      ) : null}

      {saveError ? (
        <div
          role="alert"
          className="mb-4 flex shrink-0 items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">
              {saveError.status === 412
                ? "This record changed on the server."
                : "The record could not be saved."}
            </p>
            <p className="mt-0.5 text-xs leading-5 text-destructive/85">
              {saveError.status === 412
                ? "Cancel and reopen it to review the current values before editing again."
                : saveError.message}
            </p>
          </div>
        </div>
      ) : null}

      {tabs.length > 0 ? (
        <div
          className="mb-3 flex shrink-0 gap-1 overflow-x-auto rounded-xl border bg-card p-1"
          role="tablist"
          aria-label={`${form.label} sections`}
        >
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={tab === selectedTab}
              className={cn(
                "rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                tab === selectedTab
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              onClick={() => setSelectedTab(tab)}
            >
              {tab}
            </button>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border bg-card shadow-sm">
        {mode === "update" && !record && query.isPending ? (
          <DetailSkeleton />
        ) : editorActive && (mode === "create" || record) ? (
          <div className="space-y-5 p-4 md:p-5">
            <RecordFormEditor
              api={api}
              form={{ ...form, sections: visibleSections }}
              forms={forms}
              draft={draft}
              editableFields={editableFields}
              errors={fieldErrors}
              disabled={saveMutation.isPending}
              onChange={(name, value) => {
                setDraft((current) => ({ ...current, [name]: value }))
                setFieldErrors((current) => {
                  if (!current[name]) {
                    return current
                  }
                  const next = { ...current }
                  delete next[name]
                  return next
                })
                setSaveError(null)
              }}
              onApplyValues={(values) => {
                setDraft((current) => ({ ...current, ...values }))
                setFieldErrors((current) => {
                  const next = { ...current }
                  for (const name of Object.keys(values)) {
                    delete next[name]
                  }
                  return next
                })
                setSaveError(null)
              }}
            />
            {visibleSections
              .filter(
                (
                  section,
                ): section is TidePresentationFormCollection =>
                  section.kind === "collection",
              )
              .map((section) => (
                editableCollections.has(section.name) ? (
                  <EditableCollection
                    key={`collection-${section.name}`}
                    api={api}
                    section={section}
                    forms={forms}
                    rows={collectionDrafts[section.name] ?? []}
                    errors={collectionErrors[section.name] ?? []}
                    editable
                    disabled={saveMutation.isPending}
                    onRowsChange={(rows) => {
                      setCollectionDrafts((current) => ({
                        ...current,
                        [section.name]: rows,
                      }))
                      setSaveError(null)
                    }}
                    onErrorsChange={(errors) =>
                      setCollectionErrors((current) => ({
                        ...current,
                        [section.name]: errors,
                      }))
                    }
                  />
                ) : (
                  <DetailCollection
                    key={`collection-${section.name}`}
                    api={api}
                    record={(record ?? draft) as TideRecord}
                    section={section}
                  />
                )
              ))}
          </div>
        ) : record ? (
          <div className="space-y-5 p-4 md:p-5">
            {visibleSections.map((section, index) =>
              section.kind === "group" ? (
                <DetailGroup
                  key={`group-${index}-${section.label}`}
                  api={api}
                  form={form}
                  record={record}
                  section={section}
                  writable={writable}
                />
              ) : (
                <DetailCollection
                  key={`collection-${section.name}`}
                  api={api}
                  record={record}
                  section={section}
                />
              ),
            )}
          </div>
        ) : null}
      </div>

      <footer className="mt-4 flex shrink-0 items-center justify-between gap-4">
        <div>
          {mode === "update" ? (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                disabled={!canPrevious || navigationPending || dirty}
                onClick={onPrevious}
              >
                <ChevronLeft />
                Previous
              </Button>
              <Button
                variant="outline"
                disabled={!canNext || navigationPending || dirty}
                onClick={onNext}
              >
                Next
                <ChevronRight />
              </Button>
              <span className="hidden text-xs text-muted-foreground xl:inline">
                {dirty
                  ? "Save or Cancel before navigating"
                  : "Page Up / Page Down"}
              </span>
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {editorActive ? (
            <>
              <Button
                variant="outline"
                disabled={saveMutation.isPending}
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button
                disabled={
                  saveMutation.isPending ||
                  (mode === "update" && !dirty)
                }
                onClick={save}
              >
                {saveMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Save />
                )}
                Save
              </Button>
            </>
          ) : (
            <Button className="hidden md:inline-flex" onClick={onClose}>
              Close
            </Button>
          )}
        </div>
      </footer>
    </main>
  )
}

function DetailGroup({
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
              const fieldWritable = writable.has(name)
              return (
                <div
                  key={name}
                  className={cn(
                    "min-w-0 rounded-xl border px-3.5 py-3",
                    fieldWritable
                      ? "bg-background"
                      : "border-border/75 bg-muted/35",
                  )}
                >
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium text-muted-foreground">
                      {field.label}
                    </span>
                    {!fieldWritable ? (
                      <LockKeyhole
                        className="size-3 text-muted-foreground/55"
                        aria-label={`${field.label} is read-only`}
                      />
                    ) : null}
                  </div>
                  <TideDisplayValue
                    api={api}
                    column={field}
                    record={record}
                    wrap
                    className={cn(
                      "min-h-5 text-sm",
                      field.alignment === "right" &&
                        "text-right tabular-nums",
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

function DetailCollection({
  api,
  record,
  section,
}: {
  api: TideApi
  record: TideRecord
  section: TidePresentationFormCollection
}) {
  const protectedFields = record._tide?.protected_fields ?? []
  const protectedCollection = protectedFields.includes(section.name)
  const raw = record[section.name]
  const rows = Array.isArray(raw) ? (raw as TideRecord[]) : []

  return (
    <section className="min-w-0">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{section.label}</h2>
        <Badge variant="outline">
          {protectedCollection ? "Protected" : `${rows.length} rows`}
        </Badge>
      </div>
      <div className="max-h-80 min-h-44 overflow-auto rounded-xl border">
        {protectedCollection ? (
          <div className="flex min-h-44 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LockKeyhole className="size-4" />
            This collection is protected for the current identity.
          </div>
        ) : rows.length === 0 ? (
          <div className="flex min-h-44 flex-col items-center justify-center text-sm text-muted-foreground">
            <Rows3 className="mb-2 size-5" />
            No collection records
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
    </section>
  )
}

function DetailSkeleton() {
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

function formTabs(sections: TidePresentationFormSection[]): string[] {
  if (!sections.some((section) => section.tab)) {
    return []
  }
  return [
    ...new Set(sections.map((section) => section.tab ?? "General")),
  ]
}

function collectionDraftState(
  form: TideFormPresentation,
  record?: TideRecord,
): Record<string, TideRecord[]> {
  return Object.fromEntries(
    form.sections
      .filter(
        (
          section,
        ): section is TidePresentationFormCollection =>
          section.kind === "collection" &&
          section.writable === true,
      )
      .map((section) => [
        section.name,
        collectionDraftRows(section, record?.[section.name]),
      ]),
  )
}

function issueFieldErrors(
  form: TideFormPresentation,
  issues: TideValidationIssue[],
): TideFormErrors {
  const errors: TideFormErrors = {}
  for (const issue of issues) {
    for (const name of issue.fields) {
      const field = form.fields[name]
      if (!field || errors[name]) {
        continue
      }
      errors[name] = issue.message.replace(
        new RegExp(`^${escapeRegExp(name)}\\b`, "i"),
        field.label,
      )
    }
  }
  return errors
}

function focusFirstError(
  form: TideFormPresentation,
  errors: TideFormErrors,
) {
  const name = Object.keys(errors)[0]
  if (!name) {
    return
  }
  requestAnimationFrame(() => {
    document.getElementById(formEditorId(form, name))?.focus()
  })
}

function focusFirstCollectionError(
  collectionName: string,
) {
  requestAnimationFrame(() => {
    document
      .querySelector<HTMLElement>(
        `[data-tide-collection="${collectionName}"]`,
      )
      ?.scrollIntoView({ block: "nearest" })
  })
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
