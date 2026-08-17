import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
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

import { TideDisplayValue } from "@/components/tide-display-value"
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
  TidePresentationManifest,
  TideRecord,
  TideSortInput,
} from "@/lib/contracts"
import { formatCellValue } from "@/lib/format"
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
  /** For reference cells' doors to their records. */
  views?: TidePresentationManifest["views"]
  records: TideRecord[]
  loading: boolean
  fetchingMore: boolean
  hasMore: boolean
  fetchMore: () => void
  sort: TideSortInput[]
  onSort: (sort: TideSortInput[]) => void
  selectedIdentity: unknown | null
  onSelect: (record: TideRecord) => void
  onOpen: (record: TideRecord) => void
  registerScrollReset: (reset: () => void) => void
}

export function TideDataGrid({
  api,
  application,
  principal,
  view,
  views,
  records,
  loading,
  fetchingMore,
  hasMore,
  fetchMore,
  sort,
  onSort,
  selectedIdentity,
  onSelect,
  onOpen,
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

  // One tab stop for the whole grid, on the selected row -- the ARIA grid
  // pattern, and the only one that works here. Giving every row `tabIndex={0}`
  // spent a tab stop per visible row, and since the list is virtualized the
  // rows outside the rendered window had no node to reach at all.
  //
  // Derived from the selection rather than held beside it: moving the caret
  // selects, exactly as clicking a row does, so a second piece of state would
  // only be something for the two to disagree about.
  const rows = table.getRowModel().rows
  const selectedIndex = useMemo(
    () =>
      selectedIdentity === null || selectedIdentity === undefined
        ? -1
        : rows.findIndex(
            (row) =>
              String(row.original[view.identity_field]) ===
              String(selectedIdentity),
          ),
    [rows, selectedIdentity, view.identity_field],
  )
  const activeIndex = selectedIndex >= 0 ? selectedIndex : 0
  const pendingFocus = useRef<number | null>(null)

  const moveActiveRow = useCallback(
    (index: number) => {
      const target = Math.max(0, Math.min(index, rows.length - 1))
      const row = rows[target]
      if (!row || target === activeIndex) {
        return
      }
      // Focus is applied by the effect below rather than here: the target may
      // be outside the rendered window, in which case it has no node until
      // the scroll this schedules has produced one.
      pendingFocus.current = target
      virtualizer.scrollToIndex(target)
      onSelect(row.original)
    },
    [activeIndex, onSelect, rows, virtualizer],
  )

  // No dependency list on purpose: this has to run on whichever render first
  // materializes the row, which is one or two after the key press when the
  // grid had to scroll to reach it. The ref makes every other run a no-op.
  useEffect(() => {
    const index = pendingFocus.current
    if (index === null) {
      return
    }
    const node = scrollRef.current?.querySelector<HTMLElement>(
      `[data-row-index="${index}"]`,
    )
    if (node) {
      pendingFocus.current = null
      node.focus()
    }
  })

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
      <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b bg-muted/35 px-3">
        <div className="flex min-w-0 items-center gap-2 text-xs whitespace-nowrap text-muted-foreground">
          <Rows3 className="size-3.5 shrink-0" />
          <span className="shrink-0">
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
              <span className="truncate">All matching records loaded</span>
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
        role="grid"
        aria-label={`${view.label} records`}
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
              const identity = row.original[view.identity_field]
              const selected =
                selectedIdentity !== null &&
                String(identity) === String(selectedIdentity)
              return (
                <div
                  key={row.id}
                  role="row"
                  data-row-index={virtualRow.index}
                  tabIndex={virtualRow.index === activeIndex ? 0 : -1}
                  aria-selected={selected}
                  aria-rowindex={virtualRow.index + 1}
                  className={cn(
                    "absolute top-0 left-0 grid cursor-default border-b border-border/55 text-sm outline-none hover:bg-accent/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/45",
                    selected && "bg-accent/70 hover:bg-accent/75",
                  )}
                  style={{
                    gridTemplateColumns: gridTemplate,
                    height: ROW_HEIGHT,
                    transform: `translateY(${virtualRow.start}px)`,
                    width: contentWidth,
                  }}
                  onClick={() => onSelect(row.original)}
                  onDoubleClick={() => onOpen(row.original)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault()
                      onOpen(row.original)
                    } else if (event.key === "ArrowDown") {
                      event.preventDefault()
                      moveActiveRow(virtualRow.index + 1)
                    } else if (event.key === "ArrowUp") {
                      event.preventDefault()
                      moveActiveRow(virtualRow.index - 1)
                    } else if (event.key === "Home") {
                      event.preventDefault()
                      moveActiveRow(0)
                    } else if (event.key === "End") {
                      // The last row loaded, not the last row there is: an
                      // incremental browse only knows what it has fetched, and
                      // arriving here asks for the next page the same way
                      // scrolling to the bottom does.
                      event.preventDefault()
                      moveActiveRow(rows.length - 1)
                    }
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
                        <TideDisplayValue
                          api={api}
                          column={column}
                          record={row.original}
                          views={views}
                          // The grid's roving tab stop owns the keyboard;
                          // the doors stay for the pointer and for Enter
                          // on the opened record.
                          linkTabIndex={-1}
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
