import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  BookmarkPlus,
  ChartNoAxesCombined,
  CircleAlert,
  CircleCheck,
  Filter,
  FolderOpen,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  SquarePen,
  X,
} from "lucide-react"

import { TideDataGrid } from "@/components/tide-data-grid"
import {
  MassUpdateDialog,
  massAssignableFields,
} from "@/components/mass-update-dialog"
import { Badge } from "@/components/ui/badge"
import { TideLine } from "@/components/tide-line"
import { BrowseExportControl } from "@/components/browse-export-control"
import { ColumnChooser } from "@/components/column-chooser"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { documentTitle, useDocumentTitle } from "@/lib/document-title"
import { cameFromReferenceLink } from "@/lib/reference-link"
import { useUrlParameter } from "@/lib/url-state"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFilterInput,
  TideFormPresentation,
  TidePresentationManifest,
  TidePresentationReport,
  TideRecord,
  TideSortInput,
  TideViewState,
  TideSavedView,
  TideViewStateColumn,
} from "@/lib/contracts"
import {
  EDITABLE_SCALAR_TYPES,
  changedMutationPayload,
  formDraft,
  isEditableForm,
  issueFieldErrors,
  validateFormDraft,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import {
  gridStateFilters,
  type ColumnFilterState,
} from "@/lib/grid-query"
import { applySavedView, captureSavedView } from "@/lib/saved-views"
import { cn } from "@/lib/utils"

/**
 * Loaded when a record is opened, not when the application is.
 *
 * The record screen is the larger half of this renderer -- the form editor,
 * the editable collection, the three-way conflict review and the reference
 * lookup all hang off it -- and none of it is on the path to the first screen
 * a person sees. Everything shipped in one 563 kB chunk before this, so
 * signing in paid for the editor whether or not anything was ever opened.
 *
 * Both are already rendered conditionally, which is what makes this a change
 * of import rather than of structure.
 */
const RecordDetail = lazy(async () => ({
  default: (await import("@/components/record-detail")).RecordDetail,
}))
const ReportPreview = lazy(async () => ({
  default: (await import("@/components/report-preview")).ReportPreview,
}))

interface InlineEditState {
  identity: string
  etag: string | null
  /** The fresh GET this edit started from -- its values are the diff base. */
  record: TideRecord
  draft: TideFormDraft
  editable: Set<string>
  errors: TideFormErrors
  saving: boolean
}

interface BrowseWorkspaceProps {
  api: TideApi
  application: string
  principal: string
  view: TideBrowsePresentation
  form: TideFormPresentation | null
  forms: TidePresentationManifest["forms"]
  views: TidePresentationManifest["views"]
  reports: Record<string, TidePresentationReport>
  /** Whether this session may view the entity's audit trail. */
  audit?: boolean
  /**
   * A one-shot instruction from the home surface: apply this saved view
   * once its list arrives. Read at mount only -- the workspace remounts
   * per view, so a later change of the prop is a stale instruction.
   */
  initialSavedView?: string | null
}

export function BrowseWorkspace({
  api,
  application,
  principal,
  view,
  form,
  forms,
  views,
  reports,
  audit = false,
  initialSavedView = null,
}: BrowseWorkspaceProps) {
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search.trim(), 300)
  const [filterName, setFilterName] = useState("all")
  // The selected saved view's name, for the trigger label and the
  // radio mark; its columns snapshot rides separately because
  // "follow the standing arrangement" is a real value (null).
  const [activeSavedView, setActiveSavedView] = useState<string | null>(
    null,
  )
  const [savedColumns, setSavedColumns] = useState<
    TideViewStateColumn[] | null
  >(null)
  const [savingView, setSavingView] = useState(false)
  const [saveName, setSaveName] = useState("")
  const [sort, setSort] = useState<TideSortInput[]>([])
  const [selectedIdentity, setSelectedIdentity] = useState<unknown | null>(
    null,
  )
  // The open record lives in the address bar, so it can be sent to someone,
  // survives a refresh, and answers the back button. It is carried as text
  // because that is what a URL holds; every use of it -- the GET path, the
  // report path, matching a loaded row -- already went through `String`.
  const [activeRecord, setActiveRecord] = useUrlParameter("record", "")
  const activeIdentity = activeRecord === "" ? null : activeRecord
  const setActiveIdentity = useCallback(
    (identity: unknown) =>
      setActiveRecord(
        identity === null || identity === undefined ? "" : String(identity),
      ),
    [setActiveRecord],
  )
  const [creating, setCreating] = useState(false)
  // A duplicate's head start: the create form opens seeded with these
  // values, and anything that ends or restarts creating clears them so
  // New is never accidentally a copy.
  const [duplicateSeed, setDuplicateSeed] = useState<TideRecord | null>(null)
  // A refusal wears refusal colors: one band serves both tones, and the
  // tone decides whether assistive tech hears it as a status or an alert.
  const [feedback, setFeedback] = useState<{
    tone: "success" | "error"
    message: string
  } | null>(null)
  const [navigationPending, setNavigationPending] = useState(false)
  const [previewRequest, setPreviewRequest] = useState<{
    report: TidePresentationReport
    identity: unknown | null
  } | null>(null)
  // The mass-update selection, keyed by identity so it survives sorting
  // and incremental fetches. It clears when membership changes meaning --
  // search, filters, named filter -- and the workspace remounting per view
  // covers the rest.
  const [massSelection, setMassSelection] = useState<ReadonlySet<string>>(
    () => new Set(),
  )
  const [massUpdating, setMassUpdating] = useState(false)
  const scrollReset = useRef<(() => void) | null>(null)

  const selectedFilter = view.named_filters.find(
    (candidate) => candidate.name === filterName,
  )
  // Per-column filters: one discriminated state each -- a membership
  // list (null element = the blank checkbox), a range, or a contains.
  // One active mode per column, by construction.
  const [columnFilters, setColumnFilters] = useState<
    Record<string, ColumnFilterState>
  >({})
  useEffect(() => setColumnFilters({}), [view.view])
  useEffect(() => {
    setActiveSavedView(null)
    setSavedColumns(null)
  }, [view.view])
  // The shared composition (named filter + column filters) plus this
  // screen's own live search clause -- a dashboard tile asks with the
  // same function and no search box. One builder serves the query and
  // each funnel's "every condition except my own".
  const composeFilters = useCallback(
    (except?: string): TideFilterInput[] => {
      const considered = except
        ? Object.fromEntries(
            Object.entries(columnFilters).filter(
              ([name]) => name !== except,
            ),
          )
        : columnFilters
      const result = gridStateFilters(view, {
        filterName,
        columnFilters: considered,
      })
      if (debouncedSearch && view.search_field) {
        result.push({
          field: view.search_field,
          operator: "icontains",
          value: debouncedSearch,
        })
      }
      return result
    },
    [columnFilters, debouncedSearch, filterName, view],
  )
  const filters = useMemo<TideFilterInput[]>(
    () => composeFilters(),
    [composeFilters],
  )

  const query = useInfiniteQuery({
    queryKey: [
      "browse",
      view.view,
      filters,
      sort,
      view.page_size,
    ],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.query(
        view,
        {
          filters,
          sort,
          limit: view.page_size,
          cursor: pageParam,
          // The manifest already filtered the declaration to columns this
          // principal can read, so what it says to ask is safe to ask.
          ...(view.summaries?.length ? { summaries: view.summaries } : {}),
        },
        signal,
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    staleTime: 15_000,
  })

  const records = useMemo(
    () => query.data?.pages.flatMap((page) => page.records) ?? [],
    [query.data],
  )
  // Every page answers for the whole filtered set, so the newest fetch is
  // the freshest copy of the same truth.
  const summaries = query.data?.pages.at(-1)?.summaries ?? null

  useEffect(() => {
    scrollReset.current?.()
    setSelectedIdentity(null)
    setFeedback(null)
  }, [debouncedSearch, filterName, sort, columnFilters])

  // Deliberately without `sort`: reordering the same rows does not change
  // what is selected, while a different search or filter does.
  useEffect(() => {
    setMassSelection(new Set())
  }, [debouncedSearch, filterName, columnFilters])

  const funnelControl = useMemo(
    () => ({
      active: columnFilters,
      // A column's own popup must reflect the other conditions and never
      // its own, whatever kind its own is, so an applied filter can be
      // widened from its popup.
      conditionsExcept: (field: string) => composeFilters(field),
      onApply: (field: string, filter: ColumnFilterState | null) =>
        setColumnFilters((current) => {
          const next = { ...current }
          if (filter === null) {
            delete next[field]
          } else {
            next[field] = filter
          }
          return next
        }),
    }),
    [columnFilters, composeFilters],
  )

  const identityOf = useCallback(
    (record: TideRecord) => record[view.identity_field],
    [view.identity_field],
  )
  const massUpdateDoor = view.mass_update ?? null
  const massUpdateOffered = Boolean(
    massUpdateDoor && form && massAssignableFields(form).length > 0,
  )
  const gridSelection = useMemo(
    () =>
      massUpdateOffered
        ? {
            selected: massSelection,
            onToggle: (identity: string) =>
              setMassSelection((current) => {
                const next = new Set(current)
                if (next.has(identity)) {
                  next.delete(identity)
                } else {
                  next.add(identity)
                }
                return next
              }),
            onToggleAllLoaded: () =>
              setMassSelection((current) => {
                const loaded = records.map((record) =>
                  String(record[view.identity_field]),
                )
                const everyLoaded =
                  loaded.length > 0 &&
                  loaded.every((identity) => current.has(identity))
                if (everyLoaded) {
                  const next = new Set(current)
                  for (const identity of loaded) {
                    next.delete(identity)
                  }
                  return next
                }
                return new Set([...current, ...loaded])
              }),
          }
        : null,
    [massSelection, massUpdateOffered, records, view.identity_field],
  )
  const activeIndex = records.findIndex(
    (record) =>
      activeIdentity !== null &&
      String(identityOf(record)) === String(activeIdentity),
  )
  const availableReports = Object.values(reports).filter(
    (report) => report.entity === view.entity,
  )
  const summaryReports = availableReports.filter(
    (report) => report.kind === "summary",
  )
  const recordReports = availableReports.filter(
    (report) => report.kind === "record",
  )
  const openRecord = useCallback(
    (record: TideRecord) => {
      const identity = identityOf(record)
      if (
        form &&
        identity !== null &&
        identity !== undefined &&
        view.operations.includes("get")
      ) {
        setSelectedIdentity(identity)
        setActiveIdentity(identity)
      }
    },
    [form, identityOf, setActiveIdentity, view.operations],
  )

  // --- editing in the row -------------------------------------------------
  // `edit: inline` on the view routes the open gesture here instead of the
  // record screen. The row is not a second write path: a fresh GET decides
  // what is writable, the form's own draft/validate/diff helpers shape the
  // save, and the PATCH carries the GET's version. References and
  // collections stay the form's business, so a cell edits scalars only.
  // A person's stored arrangement of this browse: which offered columns
  // it shows, in what order, under what names. Empty means the declared
  // view applies. The rows already carry every readable field, so a
  // changed arrangement never refetches records -- it redraws them.
  const chooserOffered = Boolean(view.available_columns?.length)
  const arrangementQuery = useQuery({
    queryKey: ["view-state", principal, view.view],
    queryFn: ({ signal }): Promise<TideViewState> =>
      api.viewState(view, signal),
    enabled: chooserOffered,
  })
  const arrangement = useMemo(
    () => arrangementQuery.data?.columns ?? [],
    [arrangementQuery.data],
  )
  // A saved view's snapshot outranks the standing arrangement while it
  // is selected; null follows the arrangement, and no arrangement means
  // the declared view. One variable so the grid, the chooser's draft
  // and a capture all read the same answer.
  const activeColumns = useMemo(
    () =>
      savedColumns ?? (arrangement.length ? arrangement : null),
    [arrangement, savedColumns],
  )
  const arrangedView = useMemo<TideBrowsePresentation>(() => {
    if (!activeColumns?.length) {
      return view
    }
    const offered = new Map(
      (view.available_columns ?? []).map((column) => [column.name, column]),
    )
    const columns = activeColumns.flatMap((chosen) => {
      const column = offered.get(chosen.name)
      if (!column) {
        // Stored before the offer changed -- a field renamed away or
        // newly unreadable. Skipping it keeps the rest of the
        // arrangement rather than failing the whole grid.
        return []
      }
      return [chosen.label ? { ...column, label: chosen.label } : column]
    })
    return columns.length ? { ...view, columns } : view
  }, [activeColumns, view])

  // Saved views: named grid states, fetched per browse. `null` data
  // means the server predates the capability, so nothing is offered.
  const savedViewsQuery = useQuery({
    queryKey: ["saved-views", principal, view.view],
    queryFn: ({ signal }) => api.savedViews(view, signal),
  })
  const savedViewsSupported =
    savedViewsQuery.data !== undefined && savedViewsQuery.data !== null
  const savedViewsData = savedViewsQuery.data
  const savedViews = useMemo(
    () => savedViewsData?.views ?? [],
    [savedViewsData],
  )

  // Apply the components wholesale: the controls must show exactly what
  // constrained the rows when the view was saved. One function for the
  // dropdown and the home tile, so they can never drift.
  function applyEntry(entry: TideSavedView) {
    const state = applySavedView(entry)
    setFilterName(state.filterName)
    setColumnFilters(state.columnFilters)
    setSort(state.sort)
    setSavedColumns(state.columns)
    setActiveSavedView(entry.name)
  }

  // The home tile's one-shot instruction, honoured when the list arrives.
  // A name that no longer exists opens the browse plain rather than
  // erring: the tile was drawn from the same list a moment ago.
  const pendingInitial = useRef(initialSavedView)
  useEffect(() => {
    const wanted = pendingInitial.current
    if (wanted === null || savedViews.length === 0) {
      return
    }
    pendingInitial.current = null
    const entry = savedViews.find((candidate) => candidate.name === wanted)
    if (entry) {
      applyEntry(entry)
    }
    // applyEntry reads only setters; the list arriving is the one trigger.
  }, [savedViews])

  const queryClient = useQueryClient()
  const inlineMode =
    view.edit === "inline" && form !== null && view.operations.includes("update")
  const [inlineEdit, setInlineEdit] = useState<InlineEditState | null>(null)
  useEffect(() => setInlineEdit(null), [view.view])

  const inlineDirty = useCallback(
    (state: InlineEditState) =>
      form !== null &&
      Object.keys(
        changedMutationPayload(form, state.draft, state.editable, state.record),
      ).length > 0,
    [form],
  )

  const saveInlineEdit = useCallback(async (): Promise<boolean> => {
    if (!inlineEdit || !form) {
      return true
    }
    if (inlineEdit.saving) {
      // A second save would race the first PATCH with the same If-Match
      // and turn its own success into a spurious stale-row refusal.
      return false
    }
    const clientErrors = validateFormDraft(
      form,
      inlineEdit.draft,
      inlineEdit.editable,
    )
    if (Object.keys(clientErrors).length > 0) {
      setInlineEdit({ ...inlineEdit, errors: clientErrors })
      return false
    }
    const payload = changedMutationPayload(
      form,
      inlineEdit.draft,
      inlineEdit.editable,
      inlineEdit.record,
    )
    if (Object.keys(payload).length === 0) {
      setInlineEdit(null)
      return true
    }
    setInlineEdit({ ...inlineEdit, saving: true, errors: {} })
    try {
      await api.updateRecord(
        view,
        identityOf(inlineEdit.record),
        payload,
        inlineEdit.etag,
      )
    } catch (error) {
      if (error instanceof TideApiError && error.issues.length > 0) {
        const fieldErrors = issueFieldErrors(form, error.issues)
        setInlineEdit({
          ...inlineEdit,
          saving: false,
          errors: fieldErrors,
        })
        // The row has no room for a message strip, so the field's own
        // words go to the feedback line; the cell carries the mark.
        setFeedback({
          tone: "error",
          message: Object.values(fieldErrors)[0] ?? error.message,
        })
        return false
      }
      if (error instanceof TideApiError && error.status === 412) {
        // A stale row is not this edit's to win: leave editing, say why,
        // and let the refetch show what the record has become.
        setInlineEdit(null)
        void queryClient.invalidateQueries({
          queryKey: ["browse", view.view],
        })
      } else {
        // Anything else -- the server unreachable, a fault -- never saw
        // the draft, and the draft is the user's: keep the row editing.
        setInlineEdit({ ...inlineEdit, saving: false })
      }
      setFeedback({
        tone: "error",
        message:
          error instanceof TideApiError
            ? error.message
            : "The row could not be saved.",
      })
      return false
    }
    setInlineEdit(null)
    void queryClient.invalidateQueries({ queryKey: ["browse", view.view] })
    return true
  }, [api, form, identityOf, inlineEdit, queryClient, view])

  const startInlineEdit = useCallback(
    async (record: TideRecord) => {
      if (!inlineMode || !form) {
        openRecord(record)
        return
      }
      const identity = identityOf(record)
      if (identity === null || identity === undefined) {
        return
      }
      if (inlineEdit) {
        if (String(identity) === inlineEdit.identity) {
          return
        }
        if (inlineDirty(inlineEdit)) {
          if (!(await saveInlineEdit())) {
            return
          }
        } else {
          setInlineEdit(null)
        }
      }
      setSelectedIdentity(identity)
      let snapshot
      try {
        snapshot = await api.getRecord(view, identity)
      } catch (error) {
        setFeedback({
          tone: "error",
          message:
            error instanceof TideApiError
              ? error.message
              : "The record could not be read.",
        })
        return
      }
      const tide = snapshot.record._tide
      const hidden = new Set(tide?.appearance?.hidden ?? [])
      const editable = new Set(
        view.columns
          .map((column) => column.name)
          .filter(
            (name) =>
              (tide?.writable_fields ?? []).includes(name) &&
              !hidden.has(name) &&
              form.fields[name] !== undefined &&
              form.fields[name].field_type !== "reference" &&
              EDITABLE_SCALAR_TYPES.has(form.fields[name].field_type),
          ),
      )
      if (editable.size === 0) {
        // Nothing this row can edit in place -- a fully locked record, or
        // columns that are all references -- so the form says why.
        openRecord(record)
        return
      }
      setInlineEdit({
        identity: String(identity),
        etag: snapshot.etag,
        record: snapshot.record,
        draft: formDraft(form, snapshot.record),
        editable,
        errors: {},
        saving: false,
      })
    },
    [
      api,
      form,
      identityOf,
      inlineDirty,
      inlineEdit,
      inlineMode,
      openRecord,
      saveInlineEdit,
      view,
    ],
  )

  const changeInlineEdit = useCallback((name: string, value: unknown) => {
    setInlineEdit((current) =>
      current
        ? {
            ...current,
            draft: { ...current.draft, [name]: value },
            errors: Object.fromEntries(
              Object.entries(current.errors).filter(([key]) => key !== name),
            ),
          }
        : current,
    )
  }, [])

  const selectRow = useCallback(
    (record: TideRecord) => {
      const identity = identityOf(record)
      if (inlineEdit && String(identity) !== inlineEdit.identity) {
        // Leaving a dirty row saves it, the way the reference application
        // does; a refused save keeps the editing row selected instead.
        if (inlineDirty(inlineEdit)) {
          void saveInlineEdit().then((saved) => {
            if (saved) {
              setSelectedIdentity(identity)
            }
          })
          return
        }
        setInlineEdit(null)
      }
      setSelectedIdentity(identity)
    },
    [identityOf, inlineDirty, inlineEdit, saveInlineEdit],
  )
  const navigate = useCallback(
    async (offset: -1 | 1) => {
      if (activeIdentity === null || navigationPending) {
        return
      }
      const currentIndex = records.findIndex(
        (record) =>
          String(identityOf(record)) === String(activeIdentity),
      )
      if (currentIndex < 0) {
        return
      }
      if (offset === -1) {
        const previous = records[currentIndex - 1]
        if (previous) {
          const identity = identityOf(previous)
          setSelectedIdentity(identity)
          setActiveIdentity(identity)
        }
        return
      }
      const loadedNext = records[currentIndex + 1]
      if (loadedNext) {
        const identity = identityOf(loadedNext)
        setSelectedIdentity(identity)
        setActiveIdentity(identity)
        return
      }
      if (!query.hasNextPage) {
        return
      }
      setNavigationPending(true)
      try {
        const result = await query.fetchNextPage()
        const expanded =
          result.data?.pages.flatMap((page) => page.records) ?? records
        const refreshedIndex = expanded.findIndex(
          (record) =>
            String(identityOf(record)) === String(activeIdentity),
        )
        const next = expanded[refreshedIndex + 1]
        if (next) {
          const identity = identityOf(next)
          setSelectedIdentity(identity)
          setActiveIdentity(identity)
        }
      } finally {
        setNavigationPending(false)
      }
    },
    [
      activeIdentity,
      identityOf,
      navigationPending,
      query,
      records,
      setActiveIdentity,
    ],
  )

  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The records could not be loaded.")
        : null

  // `null` while a record is open: `RecordDetail` names the tab then, and two
  // writers would only be something for the ordering to get wrong.
  useDocumentTitle(
    activeIdentity === null && !creating
      ? documentTitle(view.label, application)
      : null,
  )

  return (
    <>
    <main
      className={cn(
        "min-h-0 flex-1 flex-col p-4 md:p-6",
        activeIdentity === null && !creating ? "flex" : "hidden",
      )}
    >
      <div
        // Named so the phone-width check can measure the browse chrome without
        // measuring the grid, which scrolls horizontally on purpose.
        data-tide-toolbar
        className="mb-5 flex shrink-0 flex-col gap-4 xl:flex-row xl:items-end xl:justify-between"
      >
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              {view.label}
            </h1>
            <TideLine className="w-12 text-primary/45" />
            <Badge variant="outline">Server mode</Badge>
          </div>
          <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            Secured, incremental records from {view.entity}
          </p>
        </div>

        {/* One wrapping row at every width: on a phone the search keeps its
            own line and the actions share the next, so the records start a
            screen earlier than the stacked full-width column they replace. */}
        <div className="flex flex-wrap items-center gap-2">
          {view.search_field ? (
            <div className="relative w-full min-w-0 sm:w-72">
              <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" />
              <Input
                className="pr-9 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={`Search ${view.search_label ?? view.label}`}
                aria-label={`Search ${view.search_label ?? view.label}`}
              />
              {search ? (
                <button
                  type="button"
                  className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                  aria-label="Clear search"
                  onClick={() => setSearch("")}
                >
                  <X className="size-4" />
                </button>
              ) : null}
            </div>
          ) : null}

          {view.named_filters.length || savedViewsSupported ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant={
                    activeSavedView !== null || filterName !== "all"
                      ? "secondary"
                      : "outline"
                  }
                >
                  <Filter />
                  {activeSavedView ?? selectedFilter?.label ?? "All records"}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Named filter</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuRadioGroup
                  value={
                    activeSavedView !== null
                      ? `saved:${activeSavedView}`
                      : filterName
                  }
                  onValueChange={(value) => {
                    if (value.startsWith("saved:")) {
                      const entry = savedViews.find(
                        (candidate) => `saved:${candidate.name}` === value,
                      )
                      if (!entry) {
                        return
                      }
                      applyEntry(entry)
                      return
                    }
                    // A declared filter keeps its existing semantics --
                    // funnels compose, nothing else resets -- but
                    // leaving a saved view returns the columns to the
                    // standing arrangement.
                    setFilterName(value)
                    setSavedColumns(null)
                    setActiveSavedView(null)
                  }}
                >
                  <DropdownMenuRadioItem value="all">
                    All records
                  </DropdownMenuRadioItem>
                  {view.named_filters.map((item) => (
                    <DropdownMenuRadioItem
                      key={item.name}
                      value={item.name}
                    >
                      {item.label}
                    </DropdownMenuRadioItem>
                  ))}
                  {savedViews.length ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuLabel>Saved views</DropdownMenuLabel>
                      {savedViews.map((entry) => (
                        <DropdownMenuRadioItem
                          key={entry.name}
                          value={`saved:${entry.name}`}
                          className="group/saved pr-1"
                        >
                          <span className="flex-1 truncate">
                            {entry.name}
                          </span>
                          <button
                            type="button"
                            aria-label={`Delete saved view ${entry.name}`}
                            title={`Delete saved view ${entry.name}`}
                            className="ml-2 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover/saved:opacity-100"
                            onPointerDown={(event) => {
                              event.stopPropagation()
                              event.preventDefault()
                            }}
                            onClick={(event) => {
                              event.stopPropagation()
                              event.preventDefault()
                              void (async () => {
                                await api.deleteSavedView(view, entry.name)
                                if (activeSavedView === entry.name) {
                                  setActiveSavedView(null)
                                }
                                await queryClient.invalidateQueries({
                                  queryKey: [
                                    "saved-views",
                                    principal,
                                    view.view,
                                  ],
                                })
                                // The home catalogue is the same truth.
                                await queryClient.invalidateQueries({
                                  queryKey: [
                                    "saved-view-catalogue",
                                    principal,
                                  ],
                                })
                              })()
                            }}
                          >
                            <X className="size-3.5" />
                          </button>
                        </DropdownMenuRadioItem>
                      ))}
                    </>
                  ) : null}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}

          {savedViewsSupported ? (
            <Popover
              open={savingView}
              onOpenChange={(next) => {
                setSavingView(next)
                if (next) {
                  setSaveName(activeSavedView ?? "")
                }
              }}
            >
              <PopoverTrigger asChild>
                <Button
                  aria-label="Save current view"
                  title="Save current view"
                  size="icon"
                  variant="outline"
                >
                  <BookmarkPlus />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-72">
                <div className="mb-2 text-sm font-medium">
                  Save current view
                </div>
                <form
                  onSubmit={(event) => {
                    event.preventDefault()
                    const name = saveName.trim()
                    if (!name) {
                      return
                    }
                    void (async () => {
                      try {
                        await api.saveSavedView(
                          view,
                          captureSavedView(name, {
                            filterName,
                            columnFilters,
                            sort,
                            columns: activeColumns,
                          }),
                        )
                        await queryClient.invalidateQueries({
                          queryKey: ["saved-views", principal, view.view],
                        })
                        // The home catalogue is the same truth.
                        await queryClient.invalidateQueries({
                          queryKey: ["saved-view-catalogue", principal],
                        })
                        setActiveSavedView(name)
                        setSavedColumns(activeColumns)
                        setSavingView(false)
                      } catch (error) {
                        setFeedback({
                          tone: "error",
                          message:
                            error instanceof TideApiError
                              ? error.message
                              : "The view could not be saved.",
                        })
                        setSavingView(false)
                      }
                    })()
                  }}
                >
                  <div className="flex items-center gap-2">
                    <Input
                      aria-label="View name"
                      placeholder="Name this view"
                      value={saveName}
                      autoFocus
                      onChange={(event) => setSaveName(event.target.value)}
                    />
                    <Button
                      type="submit"
                      size="sm"
                      disabled={!saveName.trim()}
                    >
                      Save
                    </Button>
                  </div>
                </form>
              </PopoverContent>
            </Popover>
          ) : null}

          {form ? (
            <>
              {isEditableForm(form) &&
              Object.values(form.fields).some((field) => field.writable) &&
              view.operations.includes("create") ? (
                <Button
                  onClick={() => {
                    setFeedback(null)
                    setDuplicateSeed(null)
                    setCreating(true)
                  }}
                >
                  <Plus />
                  New
                </Button>
              ) : null}
              <Button
                variant="outline"
                disabled={selectedIdentity === null}
                onClick={() => {
                  const record = records.find(
                    (candidate) =>
                      String(identityOf(candidate)) ===
                      String(selectedIdentity),
                  )
                  if (record) {
                    openRecord(record)
                  }
                }}
              >
                <FolderOpen />
                Open
              </Button>
            </>
          ) : null}

          {summaryReports.map((report) => (
            <Button
              key={report.name}
              variant="outline"
              onClick={() =>
                setPreviewRequest({ report, identity: null })
              }
            >
              <ChartNoAxesCombined />
              {report.title}
            </Button>
          ))}

          {chooserOffered ? (
            <ColumnChooser
              view={view}
              state={arrangement}
              onSave={async (columns) => {
                await api.saveViewState(view, columns)
                await queryClient.invalidateQueries({
                  queryKey: ["view-state", principal, view.view],
                })
              }}
              onReset={async () => {
                await api.resetViewState(view)
                await queryClient.invalidateQueries({
                  queryKey: ["view-state", principal, view.view],
                })
              }}
            />
          ) : null}

          {massUpdateOffered && massSelection.size > 0 ? (
            <>
              <Badge variant="secondary" className="tabular-nums">
                {massSelection.size.toLocaleString()} selected
              </Badge>
              <Button
                variant="outline"
                onClick={() => setMassUpdating(true)}
              >
                <SquarePen />
                Change…
              </Button>
              <Button
                variant="ghost"
                onClick={() => setMassSelection(new Set())}
              >
                Clear selection
              </Button>
            </>
          ) : null}

          <BrowseExportControl
            api={api}
            view={view}
            filters={filters}
            sort={sort}
          />

          <Button
            aria-label="Refresh records"
            size="icon"
            variant="outline"
            onClick={() => query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw
              className={query.isFetching ? "animate-spin" : undefined}
            />
          </Button>
        </div>
      </div>

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

      {feedback ? (
        <div
          role={feedback.tone === "error" ? "alert" : "status"}
          className={cn(
            "mb-4 flex shrink-0 items-center gap-2 rounded-xl border px-4 py-3 text-sm",
            feedback.tone === "error"
              ? "border-destructive/25 bg-destructive/8 text-destructive"
              : "border-emerald-500/25 bg-emerald-500/8 text-emerald-700 dark:text-emerald-300",
          )}
        >
          {feedback.tone === "error" ? (
            <CircleAlert className="size-4" />
          ) : (
            <CircleCheck className="size-4" />
          )}
          {feedback.message}
        </div>
      ) : null}

      <TideDataGrid
        api={api}
        application={application}
        principal={principal}
        view={arrangedView}
        arranged={arrangedView !== view}
        views={views}
        records={records}
        summaries={summaries}
        columnFilters={funnelControl}
        selection={gridSelection}
        inlineEdit={
          inlineEdit && form
            ? {
                identity: inlineEdit.identity,
                draft: inlineEdit.draft,
                editable: inlineEdit.editable,
                errors: inlineEdit.errors,
                fields: form.fields,
                saving: inlineEdit.saving,
              }
            : null
        }
        onInlineChange={changeInlineEdit}
        onInlineSave={() => {
          void saveInlineEdit()
        }}
        onInlineCancel={() => setInlineEdit(null)}
        loading={query.isPending}
        fetchingMore={query.isFetchingNextPage}
        hasMore={query.hasNextPage}
        fetchMore={() => {
          void query.fetchNextPage()
        }}
        sort={sort}
        onSort={setSort}
        selectedIdentity={selectedIdentity}
        onSelect={selectRow}
        onOpen={(record) => {
          if (inlineMode) {
            void startInlineEdit(record)
          } else {
            openRecord(record)
          }
        }}
        registerScrollReset={(reset) => {
          scrollReset.current = reset
        }}
      />
    </main>
    {(activeIdentity !== null || creating) && form ? (
      <Suspense fallback={<RecordChunkLoading />}>
      <RecordDetail
        api={api}
        application={application}
        view={view}
        form={form}
        forms={forms}
        views={views}
        reports={recordReports}
        audit={audit}
        mode={creating ? "create" : "update"}
        identity={creating ? null : activeIdentity}
        seed={creating ? duplicateSeed : null}
        onDuplicate={(values) => {
          setFeedback(null)
          setDuplicateSeed(values)
          setCreating(true)
        }}
        position={Math.max(activeIndex, 0)}
        loadedCount={records.length}
        canPrevious={activeIndex > 0}
        canNext={
          activeIndex >= 0 &&
          (activeIndex < records.length - 1 || Boolean(query.hasNextPage))
        }
        navigationPending={navigationPending}
        onPrevious={() => void navigate(-1)}
        onNext={() => void navigate(1)}
        onClose={() => {
          setCreating(false)
          setDuplicateSeed(null)
          // A record reached by following a reference link closes back to
          // exactly where the person was -- the entry the link pushed is
          // marked, and one step back restores both view and record.
          if (cameFromReferenceLink()) {
            window.history.back()
            return
          }
          setActiveIdentity(null)
        }}
        onSaved={(record, mode, next) => {
          const identity = identityOf(record)
          setSelectedIdentity(identity)
          setFeedback({
            tone: "success",
            message: `${form.label} ${mode === "create" ? "created" : "updated"} successfully.`,
          })
          // `Save and New` asked for the next one, so the create screen stays
          // where it is -- with the grid refreshed underneath it, ready for
          // whenever the run ends. Either way the duplicate seed is spent:
          // the next blank must be a blank.
          setDuplicateSeed(null)
          if (mode === "create" && next !== "new") {
            setCreating(false)
            setActiveIdentity(null)
          }
          void query.refetch()
        }}
        onActionCompleted={(record) => {
          setSelectedIdentity(identityOf(record))
          void query.refetch()
        }}
        onPreviewReport={(report) =>
          setPreviewRequest({ report, identity: activeIdentity })
        }
      />
      </Suspense>
    ) : null}
    {previewRequest ? (
      <Suspense fallback={null}>
        <ReportPreview
          api={api}
          report={previewRequest.report}
          identity={previewRequest.identity}
          onClose={() => setPreviewRequest(null)}
        />
      </Suspense>
    ) : null}
    {massUpdating && massUpdateDoor && form ? (
      <MassUpdateDialog
        api={api}
        view={view}
        massUpdate={massUpdateDoor}
        form={form}
        records={records}
        selected={massSelection}
        onClose={() => setMassUpdating(false)}
        onApplied={() => {
          void query.refetch()
        }}
      />
    ) : null}
    </>
  )
}

/**
 * What the record screen looks like for the moment its chunk is in flight.
 *
 * Deliberately the shape of the screen rather than a spinner, and deliberately
 * not `DetailSkeleton` -- that lives in the chunk being waited for, so
 * importing it here would put back what the split just took out.
 */
function RecordChunkLoading() {
  return (
    <main className="flex min-h-0 flex-1 flex-col gap-4 p-4 md:p-6">
      <div className="h-8 w-72 max-w-full animate-pulse rounded-lg bg-muted" />
      <div className="min-h-0 flex-1 animate-pulse rounded-2xl bg-muted/60" />
    </main>
  )
}
