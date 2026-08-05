import {
  useEffect,
  useMemo,
  useRef,
  useState,
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
  CircleCheck,
  FileText,
  LoaderCircle,
  Play,
  Save,
  ShieldCheck,
  X,
} from "lucide-react"

import { EditableCollection } from "@/components/editable-collection"
import { RecordConflictReview } from "@/components/record-conflict-review"
import {
  RecordFormEditor,
} from "@/components/record-form-editor"
import {
  DetailCollection,
  DetailGroup,
  DetailSkeleton,
} from "@/components/record-detail-sections"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  actionApiError,
  TideApiError,
  type TideApi,
} from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormAction,
  TidePresentationManifest,
  TidePresentationReport,
  TideRecord,
  TideRecordSnapshot,
} from "@/lib/contracts"
import {
  compareRecordConflict,
  conflictFieldLabel,
  conflictRecordValues,
  resolveRecordConflict,
  type TideConflictChoice,
  type TideRecordConflict,
} from "@/lib/conflicts"
import { formatRecordDisplay } from "@/lib/format"
import {
  changedMutationPayload,
  collectionDraftState,
  collectionMutationPayload,
  collectionPayloadChanged,
  formDraft,
  formTabs,
  isEditableForm,
  issueFieldErrors,
  mutationPayload,
  validateCollectionDrafts,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import {
  focusFirstCollectionError,
  focusFirstError,
} from "@/lib/form-focus"
import { cn } from "@/lib/utils"

interface RecordDetailProps {
  api: TideApi
  view: TideBrowsePresentation
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  reports: TidePresentationReport[]
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
  onActionCompleted: (record: TideRecord, label: string) => void
  onPreviewReport: (report: TidePresentationReport) => void
}

interface SaveAttempt {
  payload: Record<string, unknown>
  fieldNames: string[]
  originalValues: Record<string, unknown>
  draftValues: Record<string, unknown>
}

interface PendingConflictReview {
  current: TideRecordSnapshot
  comparison: TideRecordConflict
  draftValues: Record<string, unknown>
  lockedFields: Set<string>
}

interface RecordActionAttempt {
  action: TidePresentationFormAction
  base: TideRecordSnapshot
  saveAttempt: SaveAttempt
  idempotencyKey: string | null
}

interface RecordActionResult {
  action: TidePresentationFormAction
  snapshot: TideRecordSnapshot
}

class RecordActionExecutionError extends Error {
  readonly apiError: TideApiError
  readonly stage: "save" | "action"
  readonly saved: TideRecordSnapshot | null

  constructor(
    apiError: TideApiError,
    stage: "save" | "action",
    saved: TideRecordSnapshot | null = null,
  ) {
    super(apiError.message)
    this.name = "RecordActionExecutionError"
    this.apiError = apiError
    this.stage = stage
    this.saved = saved
  }
}

export function RecordDetail({
  api,
  view,
  form,
  forms,
  reports,
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
  onActionCompleted,
  onPreviewReport,
}: RecordDetailProps) {
  const queryClient = useQueryClient()
  const skipNextHydration = useRef(false)
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
  const [actionError, setActionError] = useState<{
    label: string
    error: TideApiError
    savedBeforeAction: boolean
  } | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [rebaseNotice, setRebaseNotice] = useState<string | null>(null)
  const [conflictReview, setConflictReview] =
    useState<PendingConflictReview | null>(null)
  const [conflictChoices, setConflictChoices] = useState<
    Record<string, TideConflictChoice>
  >({})
  const [conflictOpen, setConflictOpen] = useState(false)
  const [conflictLoading, setConflictLoading] = useState(false)
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
      skipNextHydration.current = false
      setDraft(formDraft(form))
      setCollectionDrafts(collectionDraftState(form))
      setCollectionErrors({})
      setFieldErrors({})
      setSaveError(null)
      setActionError(null)
      setActionNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
    } else if (record && !query.isPlaceholderData) {
      if (skipNextHydration.current) {
        skipNextHydration.current = false
        return
      }
      setDraft(formDraft(form, record))
      setCollectionDrafts(collectionDraftState(form, record))
      setCollectionErrors({})
      setFieldErrors({})
      setSaveError(null)
      setActionError(null)
      setActionNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
    }
  }, [form, mode, query.isPlaceholderData, record])

  const saveMutation = useMutation({
    mutationFn: ({ payload }: SaveAttempt) =>
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
      setActionError(null)
      setActionNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
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
    onError: async (mutationError, attempt) => {
      const apiError =
        mutationError instanceof TideApiError
          ? mutationError
          : new TideApiError("The record could not be saved.")
      setSaveError(apiError)
      setFieldErrors(issueFieldErrors(form, apiError.issues))
      if (
        apiError.status === 412 &&
        mode === "update" &&
        identity !== null
      ) {
        await loadConflictReview(apiError, attempt)
      }
    },
  })
  const actionMutation = useMutation({
    mutationFn: async (
      attempt: RecordActionAttempt,
    ): Promise<RecordActionResult> => {
      let current = attempt.base
      if (Object.keys(attempt.saveAttempt.payload).length > 0) {
        try {
          current = await api.updateRecord(
            view,
            identity,
            attempt.saveAttempt.payload,
            attempt.base.etag,
          )
        } catch (error) {
          throw new RecordActionExecutionError(
            actionApiError(error, "The draft could not be saved."),
            "save",
          )
        }
      }
      const currentState =
        current.record._tide?.actions?.[attempt.action.name]
      if (!currentState?.visible || !currentState.enabled) {
        throw new RecordActionExecutionError(
          new TideApiError(
            `${attempt.action.label} is unavailable after saving the current draft.`,
            { code: "action_disabled" },
          ),
          "action",
          current === attempt.base ? null : current,
        )
      }
      try {
        return {
          action: attempt.action,
          snapshot: await api.executeAction(
            view,
            identity,
            attempt.action,
            current.etag,
            attempt.idempotencyKey,
          ),
        }
      } catch (error) {
        throw new RecordActionExecutionError(
          actionApiError(error, `${attempt.action.label} could not be completed.`),
          "action",
          current === attempt.base ? null : current,
        )
      }
    },
    onSuccess: ({ action, snapshot: completed }) => {
      setRecordSnapshot(completed)
      setFieldErrors({})
      setCollectionErrors({})
      setSaveError(null)
      setActionError(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
      setActionNotice(`${action.label} completed successfully.`)
      onActionCompleted(completed.record, action.label)
    },
    onError: async (mutationError, attempt) => {
      const failure =
        mutationError instanceof RecordActionExecutionError
          ? mutationError
          : new RecordActionExecutionError(
              actionApiError(
                mutationError,
                `${attempt.action.label} could not be completed.`,
              ),
              "action",
            )
      if (failure.saved) {
        setRecordSnapshot(failure.saved)
      }
      if (failure.apiError.status === 412) {
        if (failure.stage === "save") {
          await loadConflictReview(
            failure.apiError,
            attempt.saveAttempt,
          )
        } else {
          const baseline = failure.saved ?? attempt.base
          const originalValues = conflictRecordValues(
            form,
            collectionSections,
            baseline.record,
            attempt.saveAttempt.fieldNames,
          )
          await loadConflictReview(failure.apiError, {
            payload: {},
            fieldNames: attempt.saveAttempt.fieldNames,
            originalValues,
            draftValues: originalValues,
          })
        }
        return
      }
      setActionNotice(null)
      setActionError({
        label: attempt.action.label,
        error: failure.apiError,
        savedBeforeAction: failure.saved !== null,
      })
      setFieldErrors(issueFieldErrors(form, failure.apiError.issues))
    },
  })
  const busy =
    saveMutation.isPending ||
    actionMutation.isPending ||
    conflictLoading

  function setRecordSnapshot(next: TideRecordSnapshot) {
    if (identity === null) {
      return
    }
    skipNextHydration.current = true
    queryClient.setQueryData(
      ["record-detail", view.view, identity],
      next,
    )
    setDraft(formDraft(form, next.record))
    setCollectionDrafts(collectionDraftState(form, next.record))
  }

  async function loadConflictReview(
    staleError: TideApiError,
    attempt: SaveAttempt,
  ) {
    setConflictLoading(true)
    setActionError(null)
    setActionNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
    try {
      const current = await api.getRecord(view, identity)
      const currentValues = conflictRecordValues(
        form,
        collectionSections,
        current.record,
        attempt.fieldNames,
      )
      const currentWritable = new Set(
        current.record._tide?.writable_fields ?? [],
      )
      const review: PendingConflictReview = {
        current,
        comparison: compareRecordConflict(
          attempt.originalValues,
          currentValues,
          attempt.draftValues,
          attempt.fieldNames,
        ),
        draftValues: attempt.draftValues,
        lockedFields: new Set(
          attempt.fieldNames.filter(
            (name) => !currentWritable.has(name),
          ),
        ),
      }
      setSaveError(staleError)
      setConflictReview(review)
      setConflictOpen(true)
    } catch (reviewError) {
      setSaveError(
        reviewError instanceof TideApiError
          ? reviewError
          : new TideApiError(
              "The current record could not be loaded for conflict review.",
            ),
      )
    } finally {
      setConflictLoading(false)
    }
  }

  function preparePayload(): Record<string, unknown> | null {
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
    if (Object.keys(clientErrors).length > 0) {
      focusFirstError(form, clientErrors)
      return null
    }
    const invalidCollection = collectionSections.find((section) =>
      (nextCollectionErrors[section.name] ?? []).some(
        (errors) => Object.keys(errors).length > 0,
      ),
    )
    if (invalidCollection) {
      focusFirstCollectionError(invalidCollection.name)
      return null
    }
    return (
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
    )
  }

  function save() {
    const payload = preparePayload()
    if (payload === null) {
      return
    }
    if (mode === "update" && Object.keys(payload).length === 0) {
      return
    }
    setSaveError(null)
    setActionError(null)
    setActionNotice(null)
    setRebaseNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
    saveMutation.mutate(buildSaveAttempt(payload))
  }

  function buildSaveAttempt(
    payload: Record<string, unknown>,
  ): SaveAttempt {
    const fieldNames =
      mode === "update"
        ? [...editableFields, ...editableCollections]
        : []
    const originalValues =
      mode === "update" && record
        ? conflictRecordValues(
            form,
            collectionSections,
            record,
            fieldNames,
          )
        : {}
    return {
      payload,
      fieldNames,
      originalValues,
      draftValues: { ...originalValues, ...payload },
    }
  }

  function runAction(action: TidePresentationFormAction) {
    if (
      mode !== "update" ||
      identity === null ||
      !snapshot ||
      busy
    ) {
      return
    }
    const state = record?._tide?.actions?.[action.name]
    if (!state?.visible || (!state.enabled && !dirty)) {
      setActionError({
        label: action.label,
        error: new TideApiError(
          `${action.label} is unavailable for the current record.`,
          { code: "action_disabled" },
        ),
        savedBeforeAction: false,
      })
      return
    }
    const payload = preparePayload()
    if (payload === null) {
      return
    }
    setSaveError(null)
    setActionError(null)
    setActionNotice(null)
    setRebaseNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
    actionMutation.mutate({
      action,
      base: snapshot,
      saveAttempt: buildSaveAttempt(payload),
      idempotencyKey: action.idempotent
        ? `web:${globalThis.crypto.randomUUID()}`
        : null,
    })
  }

  function reloadConflictCurrent() {
    if (!conflictReview || identity === null) {
      return
    }
    hydrateConflictSnapshot(conflictReview.current, [])
    setRebaseNotice(
      "Current server values were loaded; the stale draft was discarded.",
    )
  }

  function applyConflictResolution() {
    if (!conflictReview || identity === null) {
      return
    }
    const resolution = resolveRecordConflict(
      conflictReview.comparison,
      conflictChoices,
    )
    if (!resolution.complete) {
      return
    }
    const retainedFields = resolution.draftFields.filter(
      (name) => !conflictReview.lockedFields.has(name),
    )
    const droppedFields = resolution.draftFields.filter((name) =>
      conflictReview.lockedFields.has(name),
    )
    hydrateConflictSnapshot(
      conflictReview.current,
      retainedFields,
      conflictReview.draftValues,
    )
    let notice = retainedFields.length
      ? "Current server values were loaded and the resolved draft changes were retained. Review them, then save again."
      : "Current server values were loaded; no draft changes were retained."
    if (droppedFields.length > 0) {
      notice += ` Workflow rules now lock: ${droppedFields
        .map((name) => conflictFieldLabel(form, collectionSections, name))
        .join(", ")}.`
    }
    setRebaseNotice(notice)
  }

  function hydrateConflictSnapshot(
    current: TideRecordSnapshot,
    retainedFields: readonly string[],
    draftValues: Record<string, unknown> = {},
  ) {
    if (identity === null) {
      return
    }
    const rebasedRecord: TideRecord = { ...current.record }
    for (const name of retainedFields) {
      rebasedRecord[name] = structuredClone(draftValues[name])
    }
    skipNextHydration.current = true
    queryClient.setQueryData(
      ["record-detail", view.view, identity],
      current,
    )
    setDraft(formDraft(form, rebasedRecord))
    setCollectionDrafts(collectionDraftState(form, rebasedRecord))
    setCollectionErrors({})
    setFieldErrors({})
    setSaveError(null)
    setActionError(null)
    setActionNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (conflictOpen || conflictLoading) {
        return
      }
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
      } else if (event.key === "Escape" && !busy && !dirty) {
        // Refused while there are unsaved changes, the same way Previous and
        // Next already are. Escape is one keystroke away from every field in
        // the form, and discarding an edit for it is not recoverable.
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [
    canNext,
    canPrevious,
    conflictLoading,
    conflictOpen,
    dirty,
    navigationPending,
    onClose,
    onNext,
    onPrevious,
    busy,
  ])

  useEffect(() => {
    if (!dirty) {
      return
    }
    // Escape is guarded above, but closing the tab, following a link or
    // reloading are not ours to intercept -- this is the only way to ask.
    function confirmLeaving(event: BeforeUnloadEvent) {
      event.preventDefault()
    }
    window.addEventListener("beforeunload", confirmLeaving)
    return () => window.removeEventListener("beforeunload", confirmLeaving)
  }, [dirty])

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
  const visibleActions =
    mode === "update"
      ? (form.actions ?? []).filter(
          (action) =>
            record?._tide?.actions?.[action.name]?.visible === true,
        )
      : []

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
          className="mb-4 flex shrink-0 items-start justify-between gap-4 rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-medium">
                {saveError.status === 412
                  ? "This record changed on the server."
                  : "The record could not be saved."}
              </p>
              <p className="mt-0.5 text-xs leading-5 text-destructive/85">
                {saveError.status === 412
                  ? conflictLoading
                    ? "Loading current values for a three-way review…"
                    : conflictReview
                      ? "Review Original, Current, and Draft values before rebasing onto the latest version."
                      : "Your stale draft remains open. Save again to refresh the conflict review."
                  : saveError.message}
              </p>
            </div>
          </div>
          {saveError.status === 412 &&
          conflictReview &&
          !conflictOpen ? (
            <Button
              className="shrink-0"
              size="sm"
              variant="outline"
              onClick={() => setConflictOpen(true)}
            >
              Review changes
            </Button>
          ) : null}
        </div>
      ) : null}

      {actionError ? (
        <div
          role="alert"
          className="mb-4 flex shrink-0 items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">
              {actionError.label} could not be completed
            </p>
            <p className="mt-0.5 text-xs leading-5 text-destructive/85">
              {actionError.savedBeforeAction
                ? `Your draft was saved, but the action failed: ${actionError.error.message}`
                : actionError.error.message}
            </p>
          </div>
        </div>
      ) : null}

      {actionNotice ? (
        <div
          role="status"
          className="mb-4 flex shrink-0 items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300"
        >
          <CircleCheck className="size-4 shrink-0" />
          {actionNotice}
        </div>
      ) : null}

      {rebaseNotice ? (
        <div
          role="status"
          className="mb-4 flex shrink-0 items-start gap-3 rounded-xl border border-primary/25 bg-primary/7 px-4 py-3 text-sm text-foreground"
        >
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
          <div>
            <p className="font-medium">Draft rebased onto current values</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {rebaseNotice}
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
              disabled={busy}
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
                if (conflictReview) {
                  setConflictReview(null)
                  setConflictChoices({})
                  setConflictOpen(false)
                } else {
                  setSaveError(null)
                }
                setActionError(null)
                setActionNotice(null)
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
                if (conflictReview) {
                  setConflictReview(null)
                  setConflictChoices({})
                  setConflictOpen(false)
                } else {
                  setSaveError(null)
                }
                setActionError(null)
                setActionNotice(null)
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
                    disabled={busy}
                    onRowsChange={(rows) => {
                      setCollectionDrafts((current) => ({
                        ...current,
                        [section.name]: rows,
                      }))
                      if (conflictReview) {
                        setConflictReview(null)
                        setConflictChoices({})
                        setConflictOpen(false)
                      } else {
                        setSaveError(null)
                      }
                      setActionError(null)
                      setActionNotice(null)
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
        <div className="flex flex-wrap items-center justify-end gap-2">
          {editorActive ? (
            <>
              <Button
                variant="outline"
                disabled={busy}
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button
                disabled={
                  busy ||
                  (mode === "update" && !dirty)
                }
                onClick={save}
              >
                {busy ? (
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
          {mode === "update"
            ? reports.map((report) => (
                <Button
                  key={report.name}
                  disabled={busy || dirty}
                  title={
                    dirty
                      ? "Save or cancel changes before previewing this report"
                      : `Preview ${report.title}`
                  }
                  variant="outline"
                  onClick={() => onPreviewReport(report)}
                >
                  <FileText />
                  Preview {report.title}
                </Button>
              ))
            : null}
          {visibleActions.map((action) => {
            const state = record?._tide?.actions?.[action.name]
            return (
              <Button
                key={action.name}
                className="bg-emerald-600 text-white hover:bg-emerald-600/90 dark:bg-emerald-600 dark:text-white"
                disabled={busy || (!state?.enabled && !dirty)}
                title={
                  !state?.enabled && !dirty
                    ? `${action.label} is unavailable for the current record`
                    : dirty
                      ? `Save the draft, then run ${action.label}`
                      : `Run ${action.label}`
                }
                onClick={() => runAction(action)}
              >
                {actionMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Play />
                )}
                {action.label}
              </Button>
            )
          })}
        </div>
      </footer>

      {conflictReview && conflictOpen ? (
        <RecordConflictReview
          form={form}
          collections={collectionSections}
          conflict={conflictReview.comparison}
          lockedFields={conflictReview.lockedFields}
          choices={conflictChoices}
          onChoice={(name, choice) =>
            setConflictChoices((current) => ({
              ...current,
              [name]: choice,
            }))
          }
          onContinueEditing={() => setConflictOpen(false)}
          onReloadCurrent={reloadConflictCurrent}
          onApply={applyConflictResolution}
        />
      ) : null}
    </main>
  )
}
