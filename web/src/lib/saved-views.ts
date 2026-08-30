// The two halves of a saved view, kept pure so they can be proved alone.
//
// Capture reads the workspace's controls into the wire document; apply
// turns a document back into the state those controls are driven by. The
// round trip is the feature: a grid restored from a saved view must show
// the same lit funnels, the same named filter, the same sort marks and
// the same columns it was saved with -- not merely the same rows.
import type {
  TideSavedView,
  TideSortInput,
  TideViewStateColumn,
} from "@/lib/contracts"

export interface GridState {
  /** The named-filter selection; "all" is the unfiltered choice. */
  filterName: string
  valueFilters: Record<string, unknown[]>
  sort: TideSortInput[]
  /**
   * The active columns snapshot: the arrangement the grid is showing, or
   * null when it shows the declared view.
   */
  columns: TideViewStateColumn[] | null
}

export function captureSavedView(name: string, state: GridState): TideSavedView {
  return {
    name,
    named_filter: state.filterName === "all" ? null : state.filterName,
    value_filters: state.valueFilters,
    sort: state.sort,
    columns: state.columns,
  }
}

export function applySavedView(entry: TideSavedView): GridState {
  return {
    filterName: entry.named_filter ?? "all",
    valueFilters: entry.value_filters,
    sort: entry.sort,
    columns: entry.columns,
  }
}
