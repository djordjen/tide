// The two halves of a saved view, kept pure so they can be proved alone.
//
// Capture reads the workspace's controls into the wire document; apply
// turns a document back into the state those controls are driven by. The
// round trip is the feature: a grid restored from a saved view must show
// the same lit funnels, the same ranges, the same named filter, the same
// sort marks and the same columns it was saved with -- not merely the
// same rows.
import type {
  TideFilterInput,
  TideSavedView,
  TideSortInput,
  TideViewStateColumn,
} from "@/lib/contracts"
import {
  columnFilterConditions,
  type ColumnFilterState,
} from "@/lib/grid-query"

export interface GridState {
  /** The named-filter selection; "all" is the unfiltered choice. */
  filterName: string
  columnFilters: Record<string, ColumnFilterState>
  sort: TideSortInput[]
  /**
   * The active columns snapshot: the arrangement the grid is showing, or
   * null when it shows the declared view.
   */
  columns: TideViewStateColumn[] | null
}

export function captureSavedView(name: string, state: GridState): TideSavedView {
  const valueFilters: Record<string, unknown[]> = {}
  const conditions: TideFilterInput[] = []
  for (const [field, filter] of Object.entries(state.columnFilters)) {
    if (filter.kind === "values") {
      valueFilters[field] = filter.values
    } else {
      conditions.push(...columnFilterConditions(field, filter))
    }
  }
  return {
    name,
    named_filter: state.filterName === "all" ? null : state.filterName,
    value_filters: valueFilters,
    sort: state.sort,
    columns: state.columns,
    // Omitted when empty, deliberately: a server one version behind
    // forbids keys it does not know, so a membership-only view still
    // saves there.
    ...(conditions.length ? { conditions } : {}),
  }
}

export function applySavedView(entry: TideSavedView): GridState {
  const columnFilters: Record<string, ColumnFilterState> = {}
  for (const [field, values] of Object.entries(entry.value_filters)) {
    columnFilters[field] = { kind: "values", values }
  }
  for (const condition of entry.conditions ?? []) {
    if (condition.operator === "icontains") {
      columnFilters[condition.field] = {
        kind: "contains",
        text: String(condition.value),
      }
      continue
    }
    // A field's gte and lte fold back into the one range state the
    // funnel shows; either side alone stays open-ended.
    const existing = columnFilters[condition.field]
    const range: ColumnFilterState =
      existing?.kind === "range"
        ? existing
        : { kind: "range", from: null, to: null }
    if (condition.operator === "gte") {
      range.from = String(condition.value)
    } else if (condition.operator === "lte") {
      range.to = String(condition.value)
    } else {
      continue
    }
    columnFilters[condition.field] = range
  }
  return {
    filterName: entry.named_filter ?? "all",
    columnFilters,
    sort: entry.sort,
    columns: entry.columns,
  }
}
