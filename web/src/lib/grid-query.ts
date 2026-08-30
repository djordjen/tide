// What constrains a browse grid, composed once for every asker.
//
// The workspace's live query, a dashboard tile, and a saved view's
// capture must agree on what a named filter and the column filters mean;
// two spellings of that rule is the defect class the one-declaration
// ruling exists for. The workspace appends its live search clause after
// this; a tile has no search box.
import type {
  TideBrowsePresentation,
  TideFilterInput,
  TideSummaryRequest,
} from "@/lib/contracts"

/**
 * One column's filter: a membership list, a range, or a contains --
 * never two at once. One active mode per column is the ratified rule,
 * and one discriminated state is what makes a second mode structurally
 * impossible rather than merely unlikely.
 */
export type ColumnFilterState =
  | { kind: "values"; values: unknown[] }
  | { kind: "range"; from: string | null; to: string | null }
  | { kind: "contains"; text: string }

export interface GridFilterState {
  /** The named-filter selection; "all" is the unfiltered choice. */
  filterName: string
  columnFilters: Record<string, ColumnFilterState>
}

/** The conditions one column's filter contributes to the query. */
export function columnFilterConditions(
  field: string,
  filter: ColumnFilterState,
): TideFilterInput[] {
  if (filter.kind === "values") {
    return [{ field, operator: "in", value: filter.values }]
  }
  if (filter.kind === "contains") {
    // The search box's own verb: case-insensitive, the way a person
    // typing a fragment means it.
    return [{ field, operator: "icontains", value: filter.text }]
  }
  const conditions: TideFilterInput[] = []
  if (filter.from !== null && filter.from !== "") {
    conditions.push({ field, operator: "gte", value: filter.from })
  }
  if (filter.to !== null && filter.to !== "") {
    conditions.push({ field, operator: "lte", value: filter.to })
  }
  return conditions
}

export function gridStateFilters(
  view: TideBrowsePresentation,
  state: GridFilterState,
): TideFilterInput[] {
  const named = view.named_filters.find(
    (candidate) => candidate.name === state.filterName,
  )
  const result: TideFilterInput[] = [...(named?.conditions ?? [])]
  for (const [field, filter] of Object.entries(state.columnFilters)) {
    result.push(...columnFilterConditions(field, filter))
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
