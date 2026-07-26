import { useEffect, useMemo, useRef, useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import {
  Filter,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react"

import { TideDataGrid } from "@/components/tide-data-grid"
import { Badge } from "@/components/ui/badge"
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
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFilterInput,
  TideSortInput,
} from "@/lib/contracts"

interface BrowseWorkspaceProps {
  api: TideApi
  application: string
  principal: string
  view: TideBrowsePresentation
}

export function BrowseWorkspace({
  api,
  application,
  principal,
  view,
}: BrowseWorkspaceProps) {
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search.trim(), 300)
  const [filterName, setFilterName] = useState("all")
  const [sort, setSort] = useState<TideSortInput[]>([])
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
  }, [debouncedSearch, filterName, sort])

  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The records could not be loaded.")
        : null

  return (
    <main className="flex min-h-0 flex-1 flex-col p-4 md:p-6">
      <div className="mb-5 flex shrink-0 flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">
              {view.label}
            </h1>
            <Badge variant="outline">Server mode</Badge>
          </div>
          <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            Secured, incremental records from {view.entity}
          </p>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          {view.search_field ? (
            <div className="relative min-w-0 sm:w-72">
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
                  className="justify-start sm:justify-center"
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

      <TideDataGrid
        api={api}
        application={application}
        principal={principal}
        view={view}
        records={records}
        loading={query.isPending}
        fetchingMore={query.isFetchingNextPage}
        hasMore={query.hasNextPage}
        fetchMore={() => {
          void query.fetchNextPage()
        }}
        sort={sort}
        onSort={setSort}
        registerScrollReset={(reset) => {
          scrollReset.current = reset
        }}
      />
    </main>
  )
}
