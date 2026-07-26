import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useQuery } from "@tanstack/react-query"
import {
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  type SortingState,
  type Updater,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  Columns3,
  Expand,
  GripVertical,
  LoaderCircle,
  RotateCcw,
  Rows3,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TidePresentationColumn,
  TidePresentationReference,
  TideRecord,
  TideSortInput,
} from "@/lib/contracts"
import {
  formatCellValue,
  formatReferenceDisplay,
} from "@/lib/format"
import {
  clampColumnWidth,
  clearColumnLayout,
  layoutStorageKey,
  loadColumnLayout,
  saveColumnLayout,
} from "@/lib/layout-preferences"
import { cn } from "@/lib/utils"

const ROW_HEIGHT = 43

interface TideDataGridProps {
  api: TideApi
  application: string
  principal: string
  view: TideBrowsePresentation
  records: TideRecord[]
  loading: boolean
  fetchingMore: boolean
  hasMore: boolean
  fetchMore: () => void
  sort: TideSortInput[]
  onSort: (sort: TideSortInput[]) => void
  registerScrollReset: (reset: () => void) => void
}

export function TideDataGrid({
  api,
  application,
  principal,
  view,
  records,
  loading,
  fetchingMore,
  hasMore,
  fetchMore,
  sort,
  onSort,
  registerScrollReset,
}: TideDataGridProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const headerRef = useRef<HTMLDivElement>(null)
  const draggedColumn = useRef<string | null>(null)
  const layoutChanged = useRef(false)
  const didInitialFit = useRef(false)
  const [containerWidth, setContainerWidth] = useState(0)
  const columnNames = useMemo(
    () => view.columns.map((column) => column.name),
    [view.columns],
  )
  const storageKey = layoutStorageKey(application, principal, view.view)
  const savedLayout = useMemo(
    () => loadColumnLayout(storageKey, columnNames),
    [columnNames, storageKey],
  )
  const defaultSizes = useMemo(
    () => defaultColumnSizes(view.columns),
    [view.columns],
  )
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(
    savedLayout?.order ?? columnNames,
  )
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({
    ...defaultSizes,
    ...savedLayout?.sizes,
  })

  const columns = useMemo<ColumnDef<TideRecord>[]>(
    () =>
      view.columns.map((column) => ({
        accessorKey: column.name,
        id: column.name,
        header: column.label,
        size: columnSizing[column.name] ?? defaultSizes[column.name],
        minSize: minimumColumnWidth(column),
        maxSize: 640,
        enableSorting: view.sortable_fields.includes(column.name),
      })),
    [
      columnSizing,
      defaultSizes,
      view.columns,
      view.sortable_fields,
    ],
  )
  const sortingState = useMemo<SortingState>(
    () =>
      sort.map((item) => ({
        id: item.field,
        desc: item.descending,
      })),
    [sort],
  )

  const table = useReactTable({
    data: records,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    enableMultiSort: false,
    columnResizeMode: "onChange",
    state: {
      columnOrder,
      columnSizing,
      sorting: sortingState,
    },
    onColumnOrderChange: (updater) => {
      layoutChanged.current = true
      setColumnOrder(updater)
    },
    onColumnSizingChange: (updater) => {
      layoutChanged.current = true
      setColumnSizing(updater)
    },
    onSortingChange: (updater: Updater<SortingState>) => {
      const next =
        typeof updater === "function" ? updater(sortingState) : updater
      onSort(
        next.slice(0, 1).map((item) => ({
          field: item.id,
          descending: item.desc,
        })),
      )
    },
    getRowId: (record, index) =>
      String(record[view.identity_field] ?? `row-${index}`),
  })

  const visibleColumns = table.getVisibleLeafColumns()
  const gridTemplate = visibleColumns
    .map((column) => `${column.getSize()}px`)
    .join(" ")
  const contentWidth = Math.max(
    containerWidth,
    visibleColumns.reduce((total, column) => total + column.getSize(), 0),
  )
  const virtualizer = useVirtualizer({
    count: table.getRowModel().rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  })
  const virtualRows = virtualizer.getVirtualItems()
  const lastVirtualIndex = virtualRows.at(-1)?.index ?? -1

  useEffect(() => {
    if (
      hasMore &&
      !fetchingMore &&
      records.length > 0 &&
      lastVirtualIndex >= records.length - 6
    ) {
      fetchMore()
    }
  }, [
    fetchMore,
    fetchingMore,
    hasMore,
    lastVirtualIndex,
    records.length,
  ])

  useEffect(() => {
    const element = scrollRef.current
    if (!element) {
      return
    }
    const update = () => setContainerWidth(element.clientWidth)
    update()
    if (typeof ResizeObserver === "undefined") {
      return
    }
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    registerScrollReset(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = 0
      }
    })
  }, [registerScrollReset])

  useEffect(() => {
    if (!layoutChanged.current) {
      return
    }
    saveColumnLayout(storageKey, {
      version: 1,
      order: columnOrder,
      sizes: columnSizing,
    })
  }, [columnOrder, columnSizing, storageKey])

  const bestFit = useCallback(
    (personal = true) => {
      const context = document.createElement("canvas").getContext("2d")
      if (context) {
        context.font = "500 13px Inter, ui-sans-serif, system-ui"
      }
      const sizes = Object.fromEntries(
        view.columns.map((column) => {
          const values = [
            column.label,
            ...records.map((record) =>
              formatCellValue(
                column,
                record[column.name],
                record._tide?.protected_fields,
              ),
            ),
          ]
          const measured = Math.max(
            ...values.map((value) =>
              context ? context.measureText(value).width : value.length * 7,
            ),
            minimumColumnWidth(column) - 34,
          )
          return [
            column.name,
            clampColumnWidth(measured + 34),
          ]
        }),
      )
      layoutChanged.current = personal
      setColumnSizing(sizes)
    },
    [records, view.columns],
  )

  useEffect(() => {
    if (
      !savedLayout &&
      records.length > 0 &&
      !didInitialFit.current
    ) {
      didInitialFit.current = true
      bestFit(false)
    }
  }, [bestFit, records.length, savedLayout])

  function fillAvailableWidth() {
    const current = visibleColumns.map((column) => ({
      id: column.id,
      size: column.getSize(),
      minimum:
        minimumColumnWidth(
          view.columns.find((item) => item.name === column.id)!,
        ),
    }))
    const total = current.reduce((sum, item) => sum + item.size, 0)
    if (!containerWidth || total >= containerWidth) {
      return
    }
    const extra = containerWidth - total
    const sizes = Object.fromEntries(
      current.map((item) => [
        item.id,
        clampColumnWidth(
          Math.max(item.minimum, item.size + extra * (item.size / total)),
        ),
      ]),
    )
    layoutChanged.current = true
    setColumnSizing((existing) => ({ ...existing, ...sizes }))
  }

  function resetLayout() {
    clearColumnLayout(storageKey)
    layoutChanged.current = false
    setColumnOrder(columnNames)
    setColumnSizing(defaultSizes)
  }

  function moveColumn(target: string) {
    const source = draggedColumn.current
    draggedColumn.current = null
    if (!source || source === target) {
      return
    }
    setColumnOrder((current) => {
      const next = current.filter((name) => name !== source)
      const index = next.indexOf(target)
      next.splice(index, 0, source)
      return next
    })
    layoutChanged.current = true
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
      <div className="flex h-10 shrink-0 items-center justify-between border-b bg-muted/35 px-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Rows3 className="size-3.5" />
          <span>
            {loading
              ? "Loading records"
              : `${records.length.toLocaleString()} loaded`}
          </span>
          {fetchingMore ? (
            <>
              <span className="text-border">•</span>
              <span className="flex items-center gap-1">
                <LoaderCircle className="size-3 animate-spin" />
                Loading more
              </span>
            </>
          ) : !hasMore && records.length ? (
            <>
              <span className="text-border">•</span>
              <span>All matching records loaded</span>
            </>
          ) : null}
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button className="h-7 px-2.5" size="sm" variant="ghost">
              <Columns3 />
              Layout
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Column layout</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => bestFit()}>
              <Expand />
              Best fit all columns
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={fillAvailableWidth}>
              <Columns3 />
              Fill available width
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={resetLayout}>
              <RotateCcw />
              Reset application layout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div
        ref={headerRef}
        className="shrink-0 overflow-hidden border-b bg-muted/55"
      >
        <div
          role="row"
          className="grid h-10 select-none"
          style={{
            gridTemplateColumns: gridTemplate,
            width: contentWidth,
          }}
        >
          {table.getFlatHeaders().map((header) => {
            const column = view.columns.find(
              (item) => item.name === header.column.id,
            )!
            const sorted = header.column.getIsSorted()
            return (
              <div
                key={header.id}
                role="columnheader"
                aria-sort={
                  sorted === "asc"
                    ? "ascending"
                    : sorted === "desc"
                      ? "descending"
                      : "none"
                }
                draggable
                className={cn(
                  "group/header relative flex min-w-0 items-center border-r border-border/70 text-xs font-semibold text-foreground last:border-r-0",
                  column.alignment === "right"
                    ? "justify-end"
                    : column.alignment === "center"
                      ? "justify-center"
                      : "justify-start",
                )}
                onDragStart={() => {
                  draggedColumn.current = header.column.id
                }}
                onDragOver={(event) => event.preventDefault()}
                onDrop={() => moveColumn(header.column.id)}
              >
                <button
                  type="button"
                  className={cn(
                    "flex h-full min-w-0 flex-1 items-center gap-1.5 px-3 outline-none hover:bg-accent/45 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 disabled:cursor-default",
                    column.alignment === "right"
                      ? "justify-end"
                      : column.alignment === "center"
                        ? "justify-center"
                        : "justify-start",
                  )}
                  disabled={!header.column.getCanSort()}
                  onClick={header.column.getToggleSortingHandler()}
                >
                  <GripVertical className="hidden size-3 shrink-0 text-muted-foreground/45 group-hover/header:block" />
                  <span className="truncate">{column.label}</span>
                  {header.column.getCanSort() ? (
                    sorted === "asc" ? (
                      <ArrowUp className="size-3 shrink-0 text-primary" />
                    ) : sorted === "desc" ? (
                      <ArrowDown className="size-3 shrink-0 text-primary" />
                    ) : (
                      <ChevronsUpDown className="size-3 shrink-0 text-muted-foreground/55" />
                    )
                  ) : null}
                </button>
                <div
                  role="separator"
                  aria-label={`Resize ${column.label}`}
                  aria-orientation="vertical"
                  className={cn(
                    "absolute top-0 right-0 z-10 h-full w-1 cursor-col-resize touch-none select-none bg-transparent hover:bg-primary/55",
                    header.column.getIsResizing() && "bg-primary",
                  )}
                  onDoubleClick={() => bestFit()}
                  onMouseDown={header.getResizeHandler()}
                  onTouchStart={header.getResizeHandler()}
                />
              </div>
            )
          })}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-auto bg-background"
        onScroll={(event) => {
          if (headerRef.current) {
            headerRef.current.scrollLeft = event.currentTarget.scrollLeft
          }
        }}
      >
        {loading && records.length === 0 ? (
          <GridSkeleton
            columns={visibleColumns.length}
            gridTemplate={gridTemplate}
            width={contentWidth}
          />
        ) : records.length === 0 ? (
          <div className="flex h-full min-h-64 flex-col items-center justify-center p-8 text-center">
            <div className="flex size-11 items-center justify-center rounded-2xl bg-muted">
              <Rows3 className="size-5 text-muted-foreground" />
            </div>
            <p className="mt-4 text-sm font-semibold">No matching records</p>
            <p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
              Change the search text or named filter. The server applied this
              query to the complete secured result.
            </p>
          </div>
        ) : (
          <div
            className="relative"
            style={{
              height: virtualizer.getTotalSize(),
              width: contentWidth,
            }}
          >
            {virtualRows.map((virtualRow) => {
              const row = table.getRowModel().rows[virtualRow.index]
              return (
                <div
                  key={row.id}
                  role="row"
                  aria-rowindex={virtualRow.index + 1}
                  className="absolute top-0 left-0 grid border-b border-border/55 text-sm hover:bg-accent/28"
                  style={{
                    gridTemplateColumns: gridTemplate,
                    height: ROW_HEIGHT,
                    transform: `translateY(${virtualRow.start}px)`,
                    width: contentWidth,
                  }}
                >
                  {row.getVisibleCells().map((cell) => {
                    const column = view.columns.find(
                      (item) => item.name === cell.column.id,
                    )!
                    return (
                      <div
                        key={cell.id}
                        role="cell"
                        className={cn(
                          "flex min-w-0 items-center border-r border-border/45 px-3 last:border-r-0",
                          column.alignment === "right"
                            ? "justify-end text-right tabular-nums"
                            : column.alignment === "center"
                              ? "justify-center text-center"
                              : "justify-start text-left",
                        )}
                      >
                        <GridCell
                          api={api}
                          column={column}
                          record={row.original}
                        />
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

function GridCell({
  api,
  column,
  record,
}: {
  api: TideApi
  column: TidePresentationColumn
  record: TideRecord
}) {
  const value = record[column.name]
  const protectedFields = record._tide?.protected_fields ?? []
  const reference = column.reference
  const referenceQuery = useQuery({
    queryKey: [
      "reference-display",
      reference?.entity,
      value,
    ],
    enabled:
      reference !== null &&
      reference !== undefined &&
      value !== null &&
      value !== undefined &&
      !protectedFields.includes(column.name),
    queryFn: ({ signal }) => {
      if (!reference) {
        throw new Error("reference contract missing")
      }
      return api.getReference(reference, value, signal)
    },
    staleTime: 300_000,
    retry: false,
  })

  let text = formatCellValue(column, value, protectedFields)
  if (reference && referenceQuery.data) {
    text = formatReferenceDisplay(reference, referenceQuery.data)
  }

  if (column.field_type === "choice" && text) {
    return (
      <Badge
        className="max-w-full truncate"
        variant="secondary"
        title={text}
      >
        {text}
      </Badge>
    )
  }
  return (
    <span
      className={cn(
        "block min-w-0 truncate",
        protectedFields.includes(column.name) &&
          "italic text-muted-foreground",
        referenceQuery.isPending && "text-muted-foreground",
      )}
      title={text}
    >
      {text || "—"}
    </span>
  )
}

function GridSkeleton({
  columns,
  gridTemplate,
  width,
}: {
  columns: number
  gridTemplate: string
  width: number
}) {
  return (
    <div className="py-0.5" style={{ width }}>
      {Array.from({ length: 10 }, (_, row) => (
        <div
          key={row}
          className="grid border-b border-border/45"
          style={{
            gridTemplateColumns: gridTemplate,
            height: ROW_HEIGHT,
          }}
        >
          {Array.from({ length: columns }, (_, column) => (
            <div
              key={column}
              className="flex items-center border-r border-border/40 px-3 last:border-r-0"
            >
              <Skeleton
                className={cn(
                  "h-3.5",
                  column % 3 === 0
                    ? "w-3/5"
                    : column % 2 === 0
                      ? "w-4/5"
                      : "w-2/3",
                )}
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function defaultColumnSizes(
  columns: readonly TidePresentationColumn[],
): ColumnSizingState {
  return Object.fromEntries(
    columns.map((column) => [
      column.name,
      defaultColumnWidth(column),
    ]),
  )
}

function defaultColumnWidth(column: TidePresentationColumn): number {
  if (column.reference) {
    return 240
  }
  if (column.field_type === "date" || column.field_type === "datetime") {
    return 140
  }
  if (column.field_type === "decimal" || column.field_type === "integer") {
    return 140
  }
  if (column.field_type === "choice" || column.field_type === "boolean") {
    return 125
  }
  return 185
}

function minimumColumnWidth(column: TidePresentationColumn): number {
  if (column.reference) {
    return 180
  }
  if (column.field_type === "date" || column.field_type === "datetime") {
    return 118
  }
  if (column.field_type === "decimal" || column.field_type === "integer") {
    return 96
  }
  return Math.max(88, column.label.length * 8 + 36)
}
