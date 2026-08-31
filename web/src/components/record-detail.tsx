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
  Copy,
  FileText,
  LoaderCircle,
  Play,
  Save,
  SaveAll,
  ShieldCheck,
  X,
} from "lucide-react"

import { EditableCollection } from "@/components/editable-collection"
import { RecordConflictReview } from "@/components/record-conflict-review"
import { RecordHistory } from "@/components/record-history"
import {
  RecordFormEditor,
} from "@/components/record-form-editor"
import {
  DetailCollection,
  DetailGroup,
  DetailSkeleton,
} from "@/components/record-detail-sections"
import {
  ActionParametersForm,
  actionOpensDialog,
} from "@/components/parameters"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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
  TideValidationIssue,
} from "@/lib/contracts"
import {
  compareRecordConflict,
  conflictFieldLabel,
  conflictRecordValues,
  resolveRecordConflict,
  type TideConflictChoice,
  type TideRecordConflict,
} from "@/lib/conflicts"
import { documentTitle, useDocumentTitle } from "@/lib/document-title"
import { cardEmphasisClass, recordEmphasis } from "@/lib/emphasis"
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
  seededFormDraft,
  validateCollectionDrafts,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import {
  focusFirstCollectionError,
  focusFirstEditor,
  focusFirstError,
} from "@/lib/form-focus"
import { cn } from "@/lib/utils"

interface RecordDetailProps {
  api: TideApi
  application: string
  view: TideBrowsePresentation
  form: TideFormPresentation
  forms: TidePresentationManifest["forms"]
  views?: TidePresentationManifest["views"]
  reports: TidePresentationReport[]
  /** Whether this session may view the entity's audit trail. */
  audit?: boolean
  mode: "create" | "update"
  identity: unknown | null
  /**
   * Values a create form opens with -- a duplicate's head start. Only
   * read in create mode; fields the seed does not carry keep their
   * declared defaults.
   */
  seed?: TideRecord | null
  /** Duplicate this record: hand the fetched draft up to be reopened as a create. */
  onDuplicate?: (values: TideRecord) => void
  position: number
  loadedCount: number
  canPrevious: boolean
  canNext: boolean
  navigationPending: boolean
  onPrevious: () => void
  onNext: () => void
  onClose: () => void
  onSaved: (
    record: TideRecord,
    mode: "create" | "update",
    next: SaveIntent,
  ) => void
  onActionCompleted: (record: TideRecord, label: string) => void
  onPreviewReport: (report: TidePresentationReport) => void
}

/** What the save was for: this record, or this one and the next. */
type SaveIntent = "close" | "new"

/**
 * The history tab's slot in the panel strip. Collection tabs are named by
 * their field, which is a plain identifier -- the dot keeps this out of
 * that namespace.
 */
const HISTORY_TAB = "_tide.history"

interface SaveAttempt {
  payload: Record<string, unknown>
  fieldNames: string[]
  originalValues: Record<string, unknown>
  draftValues: Record<string, unknown>
  intent: SaveIntent
  /** Warning rule ids this attempt accepts; the retry after Save anyway. */
  acknowledgedWarnings?: readonly string[]
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
  parameters: Record<string, string>
  /**
   * Warning rule ids this attempt accepts, applied to the pre-save and the
   * action commit alike -- either can gate, and the set accumulates.
   */
  acknowledgedWarnings?: readonly string[]
}

interface RecordActionResult {
  action: TidePresentationFormAction
  snapshot: TideRecordSnapshot
}

/**
 * The issues, when a refusal is one a person may accept: 422 with at least
 * one issue and nothing harder than a warning in it. Anything else is not
 * this panel's business.
 */
function warningOnlyIssues(error: TideApiError): TideValidationIssue[] | null {
  if (error.status !== 422 || error.issues.length === 0) {
    return null
  }
  return error.issues.every((issue) => issue.severity === "warning")
    ? error.issues
    : null
}

function withWarningRules(
  acknowledged: readonly string[] | undefined,
  warnings: TideValidationIssue[],
): string[] {
  return [
    ...new Set([
      ...(acknowledged ?? []),
      ...warnings.map((issue) => issue.rule),
    ]),
  ]
}

/** Info-severity notices on a written record; the acknowledged warnings are
 * deliberately not read back -- the person just confirmed them. */
function recordInfoNotices(record: TideRecord): string[] {
  return (record._tide?.notices ?? [])
    .filter((issue) => issue.severity === "info")
    .map((issue) => issue.message)
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
  application,
  view,
  form,
  forms,
  views,
  reports,
  audit = false,
  mode,
  identity,
  seed = null,
  onDuplicate,
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
  // Which collection's tab is open in the panel below the record. Null means
  // "the first one"; the choice survives Previous/Next inside one form and
  // resets when the form itself changes.
  const [activeCollection, setActiveCollection] = useState<string | null>(
    null,
  )
  useEffect(() => {
    setActiveCollection(null)
  }, [form.view])
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
  // A warning-only refusal waiting to be weighed: what the rules said, the
  // wording on the confirming button, and the retry that reruns the same
  // door with the warnings acknowledged.
  const [warningGate, setWarningGate] = useState<{
    messages: string[]
    confirmLabel: string
    retry: () => void
  } | null>(null)
  // Info-severity notices from the last successful write. Advisory only,
  // and gone with the screen: a save that closes the record takes them.
  const [infoNotices, setInfoNotices] = useState<string[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  // Which action's parameter popover is open; at most one at a time.
  const [parameterAction, setParameterAction] = useState<string | null>(null)
  const [duplicating, setDuplicating] = useState(false)
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
      setDraft(seed ? seededFormDraft(form, seed) : formDraft(form))
      setCollectionDrafts(collectionDraftState(form, seed ?? undefined))
      setCollectionErrors({})
      setFieldErrors({})
      setSaveError(null)
      setActionError(null)
      setNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
    } else if (record && !query.isPlaceholderData) {
      // update-mode hydration below; the create branch above owns `seed`.
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
      setWarningGate(null)
      setInfoNotices([])
      setNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
    }
  }, [form, mode, query.isPlaceholderData, record, seed])

  const saveMutation = useMutation({
    mutationFn: ({ payload, acknowledgedWarnings }: SaveAttempt) =>
      mode === "create"
        ? api.createRecord(
            view,
            payload,
            undefined,
            acknowledgedWarnings ?? [],
          )
        : api.updateRecord(
            view,
            identity,
            payload,
            snapshot?.etag ?? null,
            undefined,
            acknowledgedWarnings ?? [],
          ),
    onSuccess: (saved, attempt) => {
      setFieldErrors({})
      setSaveError(null)
      setActionError(null)
      setNotice(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
      setWarningGate(null)
      setInfoNotices(recordInfoNotices(saved.record))
      if (mode === "update" && identity !== null) {
        queryClient.setQueryData(
          ["record-detail", view.view, identity],
          saved,
        )
        setDraft(formDraft(form, saved.record))
        setCollectionDrafts(collectionDraftState(form, saved.record))
        // The save this panel may be showing history for just extended it.
        void queryClient.invalidateQueries({
          queryKey: ["record-history", view.view, identity],
        })
      }
      if (mode === "create" && attempt.intent === "new") {
        // The next one, from the model rather than from what was just typed:
        // a fresh draft carries the author's defaults back in, which a
        // cleared copy of the saved record would not.
        setDraft(formDraft(form))
        setCollectionDrafts(collectionDraftState(form))
        setCollectionErrors({})
        setNotice(`${form.label} created. Continue with the next one.`)
        focusFirstEditor(form, editableFields)
      }
      onSaved(saved.record, mode, attempt.intent)
    },
    onError: async (mutationError, attempt) => {
      const apiError =
        mutationError instanceof TideApiError
          ? mutationError
          : new TideApiError("The record could not be saved.")
      const warnings = warningOnlyIssues(apiError)
      if (warnings) {
        // Weighed, not failed: no red banner, no red fields. Confirming
        // reruns the same attempt with every raised warning acknowledged
        // on top of what it already carried.
        const acknowledged = withWarningRules(
          attempt.acknowledgedWarnings,
          warnings,
        )
        setWarningGate({
          messages: warnings.map((issue) => issue.message),
          confirmLabel: "Save anyway",
          retry: () =>
            saveMutation.mutate({
              ...attempt,
              acknowledgedWarnings: acknowledged,
            }),
        })
        return
      }
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
            undefined,
            attempt.acknowledgedWarnings ?? [],
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
            attempt.parameters,
            undefined,
            attempt.acknowledgedWarnings ?? [],
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
      void queryClient.invalidateQueries({
        queryKey: ["record-history", view.view, identity],
      })
      setFieldErrors({})
      setCollectionErrors({})
      setSaveError(null)
      setActionError(null)
      setRebaseNotice(null)
      setConflictReview(null)
      setConflictChoices({})
      setConflictOpen(false)
      setWarningGate(null)
      setInfoNotices(recordInfoNotices(completed.record))
      setNotice(`${action.label} completed successfully.`)
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
            intent: "close",
          })
        }
        return
      }
      const warnings = warningOnlyIssues(failure.apiError)
      if (warnings) {
        // The pre-save may already have landed; the retry starts from the
        // refreshed snapshot with nothing left to pre-save, and mints a
        // fresh idempotency key -- a refused attempt burns its key.
        const base = failure.saved ?? attempt.base
        const acknowledged = withWarningRules(
          attempt.acknowledgedWarnings,
          warnings,
        )
        setWarningGate({
          messages: warnings.map((issue) => issue.message),
          confirmLabel: `${attempt.action.label} anyway`,
          retry: () =>
            actionMutation.mutate({
              ...attempt,
              base,
              saveAttempt: failure.saved
                ? { ...attempt.saveAttempt, payload: {} }
                : attempt.saveAttempt,
              idempotencyKey: attempt.action.idempotent
                ? `web:${globalThis.crypto.randomUUID()}`
                : null,
              acknowledgedWarnings: acknowledged,
            }),
        })
        return
      }
      setNotice(null)
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
    setNotice(null)
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

  function save(intent: SaveIntent = "close") {
    const payload = preparePayload()
    if (payload === null) {
      return
    }
    if (mode === "update" && Object.keys(payload).length === 0) {
      return
    }
    setSaveError(null)
    setActionError(null)
    setWarningGate(null)
    setInfoNotices([])
    setNotice(null)
    setRebaseNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
    saveMutation.mutate(buildSaveAttempt(payload, intent))
  }

  function buildSaveAttempt(
    payload: Record<string, unknown>,
    intent: SaveIntent = "close",
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
      intent,
    }
  }

  async function startDuplicate() {
    if (identity === null || busy || duplicating || !onDuplicate) {
      return
    }
    setDuplicating(true)
    try {
      // The records service owns what copies; this only asks and hands
      // the answer up to be reopened as a create.
      onDuplicate(await api.duplicateDraft(view, identity))
    } catch (error) {
      setSaveError(
        actionApiError(error, "The record could not be duplicated."),
      )
    } finally {
      setDuplicating(false)
    }
  }

  function runAction(
    action: TidePresentationFormAction,
    parameters: Record<string, string>,
  ) {
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
    setWarningGate(null)
    setInfoNotices([])
    setNotice(null)
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
      parameters,
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
    setNotice(null)
    setConflictReview(null)
    setConflictChoices({})
    setConflictOpen(false)
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // A key another layer already consumed -- an open dropdown closing
      // itself, the report preview dismissing -- is not this screen's.
      if (conflictOpen || conflictLoading || event.defaultPrevented) {
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
  // With no declared tabs, collections leave the record card for their own
  // tabbed panel below it: the record's fields keep the full card width, and
  // a second collection is a visible tab rather than more page. A YAML tab
  // layout keeps its declared shape and renders sections exactly where the
  // author put them.
  const splitCollections =
    tabs.length === 0
      ? visibleSections.filter(
          (
            section,
          ): section is TidePresentationFormCollection =>
            section.kind === "collection",
        )
      : []
  // History is framework chrome, not one of the form's collections, but the
  // panel is renderer-owned geography and its strip already names the
  // pattern -- so where the session grants the audit trail, it is one more
  // tab there. A YAML tab layout keeps its declared sections; the panel then
  // exists for history alone.
  const historyAvailable = audit && mode === "update" && identity !== null
  const panelTabs = [
    ...splitCollections.map((section) => section.name),
    ...(historyAvailable ? [HISTORY_TAB] : []),
  ]
  const openPanelTab =
    activeCollection !== null && panelTabs.includes(activeCollection)
      ? activeCollection
      : (panelTabs[0] ?? null)
  const historyOpen = openPanelTab === HISTORY_TAB
  const openCollection =
    splitCollections.find(
      (section) => section.name === openPanelTab,
    ) ?? null
  const cardSections =
    splitCollections.length > 0
      ? visibleSections.filter((section) => section.kind === "group")
      : visibleSections
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
  const recordVerdict = recordEmphasis(record)
  const visibleActions =
    mode === "update"
      ? (form.actions ?? []).filter(
          (action) =>
            record?._tide?.actions?.[action.name]?.visible === true,
        )
      : []
  // One derivation, used by the heading and by the browser tab. Two would be
  // one thing said twice, which in this repository means one of them drifting.
  const heading =
    mode === "create"
      ? `New ${form.label}`
      : `${form.label} — ${display || String(identity)}`

  // While this is mounted the tab is its to name; the browse yields.
  useDocumentTitle(documentTitle(heading, application))

  // One renderer for a collection wherever it appears -- inline in a declared
  // tab layout, or in the default collections panel -- so the draft handlers
  // exist once.
  function renderCollectionSection(
    section: TidePresentationFormCollection,
    withHeading: boolean,
  ) {
    if (
      editorActive &&
      (mode === "create" || record) &&
      editableCollections.has(section.name)
    ) {
      return (
        <EditableCollection
          key={`collection-${section.name}`}
          api={api}
          section={section}
          forms={forms}
          views={views}
          rows={collectionDrafts[section.name] ?? []}
          errors={collectionErrors[section.name] ?? []}
          editable
          heading={withHeading}
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
            setNotice(null)
          }}
          onErrorsChange={(errors) =>
            setCollectionErrors((current) => ({
              ...current,
              [section.name]: errors,
            }))
          }
        />
      )
    }
    const source = editorActive
      ? ((record ?? draft) as TideRecord)
      : record
    if (!source) {
      return null
    }
    return (
      <DetailCollection
        key={`collection-${section.name}`}
        api={api}
        record={source}
        section={section}
        heading={withHeading}
        views={views}
      />
    )
  }

  return (
    <main className="flex min-h-0 flex-1 flex-col p-4 md:p-6">
      <header className="mb-4 flex shrink-0 items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display truncate text-2xl font-semibold tracking-tight">
              {heading}
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

      {warningGate ? (
        <div
          role="alert"
          className="mb-4 flex shrink-0 flex-wrap items-start justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200"
        >
          <div className="flex min-w-0 items-start gap-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0">
              {warningGate.messages.map((message) => (
                <p key={message} className="leading-5">
                  {message}
                </p>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setWarningGate(null)}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={warningGate.retry}>
              {warningGate.confirmLabel}
            </Button>
          </div>
        </div>
      ) : null}

      {infoNotices.length > 0 ? (
        <div
          role="status"
          className="mb-4 flex shrink-0 items-start gap-3 rounded-xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground"
        >
          <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          <div>
            {infoNotices.map((message) => (
              <p key={message} className="leading-5">
                {message}
              </p>
            ))}
          </div>
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

      {notice ? (
        <div
          role="status"
          className="mb-4 flex shrink-0 items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300"
        >
          <CircleCheck className="size-4 shrink-0" />
          {notice}
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
          className="mb-3 flex shrink-0 gap-1 overflow-x-auto border-b"
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
                "-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                tab === selectedTab
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
              )}
              onClick={() => setSelectedTab(tab)}
            >
              {tab}
            </button>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto">
        {/* The card wears the record's own verdict as the left edge the grid
            row wore, so opening a marked row does not lose the mark. */}
        <div
          data-tide-record-card
          data-testid="record-card"
          data-emphasis={recordVerdict}
          className={cn(
            "rounded-2xl border bg-card shadow-sm",
            cardEmphasisClass(recordVerdict),
          )}
        >
        {mode === "update" && !record && query.isPending ? (
          <DetailSkeleton />
        ) : editorActive && (mode === "create" || record) ? (
          // No padding of its own: each group carries a full-bleed caption
          // band and pads its fields, so the card divides into captioned
          // panels the way the reference application's do.
          <div className="divide-y">
            <RecordFormEditor
              api={api}
              form={{ ...form, sections: cardSections }}
              forms={forms}
              views={views}
              recordView={view}
              identity={identity}
              appearance={record?._tide?.appearance?.fields}
              hidden={record?._tide?.appearance?.hidden}
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
                setNotice(null)
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
                setNotice(null)
              }}
            />
            {cardSections
              .filter(
                (
                  section,
                ): section is TidePresentationFormCollection =>
                  section.kind === "collection",
              )
              .map((section) => (
                <div
                  key={`inline-${section.name}`}
                  className="p-4 md:p-5"
                >
                  {renderCollectionSection(section, true)}
                </div>
              ))}
          </div>
        ) : record ? (
          <div className="divide-y">
            {cardSections.map((section, index) =>
              section.kind === "group" ? (
                <DetailGroup
                  key={`group-${index}-${section.label}`}
                  api={api}
                  form={form}
                  record={record}
                  section={section}
                  writable={writable}
                  views={views}
                  recordView={view}
                  identity={identity}
                />
              ) : (
                <div
                  key={`inline-${section.name}`}
                  className="p-4 md:p-5"
                >
                  {renderCollectionSection(section, true)}
                </div>
              ),
            )}
          </div>
        ) : null}
        </div>

        {openPanelTab &&
        (record || (editorActive && mode === "create")) ? (
          // The panel below the record: a sibling of the record card, so the
          // rows get the full width the record's own fields keep. Every
          // collection is a tab -- the strip shows even for one, naming the
          // panel and the pattern a second collection joins -- and the
          // record's history, where granted, is the tab after them.
          <section aria-label={`${form.label} details`}>
            <div
              role="tablist"
              aria-label={`${form.label} details`}
              className="mb-3 flex gap-1 overflow-x-auto border-b"
            >
              {splitCollections.map((section) => (
                <button
                  key={section.name}
                  id={`collection-tab-${section.name}`}
                  type="button"
                  role="tab"
                  aria-selected={section.name === openPanelTab}
                  aria-controls={`collection-panel-${section.name}`}
                  className={cn(
                    "-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                    section.name === openPanelTab
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                  )}
                  onClick={() => setActiveCollection(section.name)}
                >
                  {section.label}
                </button>
              ))}
              {historyAvailable ? (
                <button
                  id="collection-tab-history"
                  type="button"
                  role="tab"
                  aria-selected={historyOpen}
                  aria-controls="collection-panel-history"
                  className={cn(
                    "-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                    historyOpen
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                  )}
                  onClick={() => setActiveCollection(HISTORY_TAB)}
                >
                  History
                </button>
              ) : null}
            </div>
            <div
              id={
                historyOpen
                  ? "collection-panel-history"
                  : `collection-panel-${openPanelTab}`
              }
              role="tabpanel"
              aria-labelledby={
                historyOpen
                  ? "collection-tab-history"
                  : `collection-tab-${openPanelTab}`
              }
            >
              {historyOpen ? (
                <RecordHistory
                  api={api}
                  view={view}
                  form={form}
                  identity={identity}
                />
              ) : openCollection ? (
                renderCollectionSection(openCollection, false)
              ) : null}
            </div>
          </section>
        ) : null}
      </div>

      {/* Wraps. Unwrapped, the two groups shared one unbreakable line: at
          375px the actions were laid out from 249px to 416px and clipped
          rather than scrolled, so `Cancel`, `Save`, `Preview` and the domain
          actions could not be reached by any means -- while the document
          reported no horizontal overflow at all, which is why nothing that
          asks the page how wide it is had ever noticed.

          Letting the groups shrink is not enough on its own and was the first
          thing tried: it brings the buttons back inside the viewport and
          prints them across each other. They need a second line, so the
          navigation group takes `min-w-0` to give the wrap somewhere to
          happen, and the actions keep `ml-auto` to stay right-aligned once
          they have a line of their own. */}
      <footer className="mt-4 flex shrink-0 flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {mode === "update" ? (
            <div className="flex items-center gap-2">
              {/* Icon-only, like the reference application's record
                  navigation: the chevrons say everything the words said,
                  and the words were width. The names live on as labels. */}
              <Button
                variant="outline"
                size="icon"
                aria-label="Previous"
                title="Previous record"
                disabled={!canPrevious || navigationPending || dirty}
                onClick={onPrevious}
              >
                <ChevronLeft />
              </Button>
              <Button
                variant="outline"
                size="icon"
                aria-label="Next"
                title="Next record"
                disabled={!canNext || navigationPending || dirty}
                onClick={onNext}
              >
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
        <div className="ml-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          {editorActive ? (
            <>
              <Button
                variant="outline"
                disabled={busy}
                onClick={onClose}
              >
                Cancel
              </Button>
              {/* Only where there is a next record to start. Entry comes in
                  runs, and this is the run: the record is written and the
                  form comes back empty rather than closing to the grid and
                  being reopened for the one after it. */}
              {mode === "create" ? (
                <Button
                  variant="outline"
                  disabled={busy}
                  title="Save this record and start another"
                  onClick={() => save("new")}
                >
                  {busy ? (
                    <LoaderCircle className="animate-spin" />
                  ) : (
                    <SaveAll />
                  )}
                  Save and New
                </Button>
              ) : null}
              <Button
                disabled={
                  busy ||
                  (mode === "update" && !dirty)
                }
                onClick={() => save()}
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
          {mode === "update" &&
          onDuplicate &&
          view.operations.includes("create") ? (
            <Button
              disabled={busy || dirty || duplicating}
              title={
                dirty
                  ? "Save or cancel changes before duplicating this record"
                  : `Duplicate this ${form.label.toLowerCase()} as a new draft`
              }
              variant="outline"
              onClick={() => void startDuplicate()}
            >
              {duplicating ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Copy />
              )}
              Duplicate
            </Button>
          ) : null}
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
                  {/* The record is the scope, so one report needs no name;
                      two must say which is which. */}
                  {reports.length === 1
                    ? "Preview"
                    : `Preview ${report.title}`}
                </Button>
              ))
            : null}
          {visibleActions.map((action) => {
            const state = record?._tide?.actions?.[action.name]
            const button = (
              <Button
                key={action.name}
                // While the draft is dirty the natural next step is Save, and
                // two filled buttons shout over each other -- the domain
                // action steps back to an outline until the record is clean.
                variant={dirty ? "outline" : "default"}
                className={cn(
                  !dirty &&
                    "bg-emerald-600 text-white hover:bg-emerald-600/90 dark:bg-emerald-600 dark:text-white",
                )}
                disabled={busy || (!state?.enabled && !dirty)}
                title={
                  !state?.enabled && !dirty
                    ? `${action.label} is unavailable for the current record`
                    : dirty
                      ? `Save the draft, then run ${action.label}`
                      : `Run ${action.label}`
                }
                onClick={
                  actionOpensDialog(action)
                    ? undefined
                    : () => runAction(action, {})
                }
              >
                {actionMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Play />
                )}
                {action.label}
              </Button>
            )
            if (!actionOpensDialog(action)) {
              return button
            }
            // A required parameter is a question: the button opens the
            // form, and running it from there executes with the answers.
            return (
              <Popover
                key={action.name}
                open={parameterAction === action.name}
                onOpenChange={(next) =>
                  setParameterAction(next ? action.name : null)
                }
              >
                <PopoverTrigger asChild>{button}</PopoverTrigger>
                <PopoverContent align="end" className="w-72">
                  <div className="mb-2 text-sm font-medium">
                    {action.label}
                  </div>
                  <ActionParametersForm
                    action={action}
                    onRun={(parameters) => {
                      setParameterAction(null)
                      runAction(action, parameters)
                    }}
                  />
                </PopoverContent>
              </Popover>
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
