// What constrains a browse grid, composed once for every asker.
//
// The workspace's live query and a dashboard tile must agree on what a
// named filter and the funnels mean; two spellings of that rule is the
// defect class the one-declaration ruling exists for. The workspace
// appends its live search clause after this; a tile has no search box.
import type {
  TideBrowsePresentation,
  TideFilterInput,
  TideSummaryRequest,
} from "@/lib/contracts"

export interface GridFilterState {
  /** The named-filter selection; "all" is the unfiltered choice. */
  filterName: string
  valueFilters: Record<string, unknown[]>
}

export function gridStateFilters(
  view: TideBrowsePresentation,
  state: GridFilterState,
): TideFilterInput[] {
  const named = view.named_filters.find(
    (candidate) => candidate.name === state.filterName,
  )
  const result: TideFilterInput[] = [...(named?.conditions ?? [])]
  for (const [field, values] of Object.entries(state.valueFilters)) {
    result.push({ field, operator: "in", value: values })
  }
  return result
}

/**
 * The numbers a tile asks for: the view's declared summaries, guaranteed
 * to include a count. When the declaration carries none, a count over the
 * identity field leads -- every tile can say how many, whatever else the
 * browse declares.
 */
export function tileSummaries(
  view: TideBrowsePresentation,
): TideSummaryRequest[] {
  const declared = view.summaries ?? []
  if (declared.some((summary) => summary.function === "count")) {
    return [...declared]
  }
  return [
    { field: view.identity_field, function: "count" },
    ...declared,
  ]
}
