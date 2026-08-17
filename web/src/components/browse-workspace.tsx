import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import {
  ChartNoAxesCombined,
  CircleCheck,
  Filter,
  FolderOpen,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react"

import { TideDataGrid } from "@/components/tide-data-grid"
import { Badge } from "@/components/ui/badge"
import { TideLine } from "@/components/tide-line"
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
} from "@/lib/contracts"
import { isEditableForm } from "@/lib/form-draft"
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

interface BrowseWorkspaceProps {
  api: TideApi
  application: string
  principal: string
  view: TideBrowsePresentation
  form: TideFormPresentation | null
  forms: TidePresentationManifest["forms"]
  views: TidePresentationManifest["views"]
  reports: Record<string, TidePresentationReport>
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
}: BrowseWorkspaceProps) {
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search.trim(), 300)
  const [filterName, setFilterName] = useState("all")
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
  const [feedback, setFeedback] = useState<string | null>(null)
  const [navigationPending, setNavigationPending] = useState(false)
  const [previewRequest, setPreviewRequest] = useState<{
    report: TidePresentationReport
    identity: unknown | null
  } | null>(null)
  const scrollReset = useRef<(() => void) | null>(null)

  const selectedFilter = view.named_filters.find(
    (candidate) => candidate.name === filterName,
  )
  const filters = useMemo<TideFilterInput[]>(() => {
    const result = [...(selectedFilter?.conditions ?? [])]
    if (debouncedSearch && view.search_field) {
      result.push({
        field: view.search_field,
        operator: "icontains",
        value: debouncedSearch,
      })
    }
    return result
  }, [debouncedSearch, selectedFilter, view.search_field])

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

  useEffect(() => {
    scrollReset.current?.()
    setSelectedIdentity(null)
    setFeedback(null)
  }, [debouncedSearch, filterName, sort])

  const identityOf = useCallback(
    (record: TideRecord) => record[view.identity_field],
    [view.identity_field],
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

          {view.named_filters.length ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant={filterName === "all" ? "outline" : "secondary"}
                >
                  <Filter />
                  {selectedFilter?.label ?? "All records"}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Named filter</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuRadioGroup
                  value={filterName}
                  onValueChange={setFilterName}
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
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}

          {form ? (
            <>
              {isEditableForm(form) &&
              Object.values(form.fields).some((field) => field.writable) &&
              view.operations.includes("create") ? (
                <Button
                  onClick={() => {
                    setFeedback(null)
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
          role="status"
          className="mb-4 flex shrink-0 items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300"
        >
          <CircleCheck className="size-4" />
          {feedback}
        </div>
      ) : null}

      <TideDataGrid
        api={api}
        application={application}
        principal={principal}
        view={view}
        views={views}
        records={records}
        loading={query.isPending}
        fetchingMore={query.isFetchingNextPage}
        hasMore={query.hasNextPage}
        fetchMore={() => {
          void query.fetchNextPage()
        }}
        sort={sort}
        onSort={setSort}
        selectedIdentity={selectedIdentity}
        onSelect={(record) => setSelectedIdentity(identityOf(record))}
        onOpen={openRecord}
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
        mode={creating ? "create" : "update"}
        identity={creating ? null : activeIdentity}
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
          // A record reached by following a reference link closes back to
          // exactly where the person was -- the entry the link pushed is
          // marked, and one step back restores both view and record.
          if (cameFromReferenceLink()) {
            window.history.back()
            return
          }
          setActiveIdentity(null)
        }}
        onSaved={(record, mode) => {
          const identity = identityOf(record)
          setSelectedIdentity(identity)
          setFeedback(
            `${form.label} ${mode === "create" ? "created" : "updated"} successfully.`,
          )
          if (mode === "create") {
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
