import { useState } from "react"
import { Filter } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideDistinctValue,
  TideFilterInput,
  TidePresentationColumn,
} from "@/lib/contracts"
import { formatCellValue } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * One column's funnel: the checkbox list of its distinct values.
 *
 * The list is the server's answer under the *other* active conditions --
 * never the column's own, so an applied filter arrives with its values
 * checked and can be widened. Checks are staged until Apply; checking
 * everything releases the column, because a filter that admits every
 * value is not a filter.
 */

interface ColumnValueFilterProps {
  api: TideApi
  view: TideBrowsePresentation
  column: TidePresentationColumn
  /** The applied values, or null when the column is unconstrained. */
  active: unknown[] | null
  /** Every active condition except this column's own value filter. */
  otherConditions: TideFilterInput[]
  onApply: (values: unknown[] | null) => void
}

export function ColumnValueFilter({
  api,
  view,
  column,
  active,
  otherConditions,
  onApply,
}: ColumnValueFilterProps) {
  const [open, setOpen] = useState(false)
  const [loaded, setLoaded] = useState<{
    values: TideDistinctValue[]
    truncated: boolean
  } | null>(null)
  const [failed, setFailed] = useState(false)
  const [staged, setStaged] = useState<Set<unknown>>(new Set())
  const [search, setSearch] = useState("")

  async function openAndLoad() {
    setLoaded(null)
    setFailed(false)
    setSearch("")
    try {
      const answer = await api.distinct(view, column.name, otherConditions)
      setLoaded({ values: answer.values, truncated: answer.truncated })
      // No filter means everything is chosen; an applied one arrives as
      // it was applied.
      setStaged(
        new Set(
          active ?? answer.values.map((item) => item.value),
        ),
      )
    } catch {
      setFailed(true)
    }
  }

  function labelFor(item: TideDistinctValue): string {
    if (item.value === null || item.value === undefined) {
      return "(Blank)"
    }
    if (item.display) {
      return item.display
    }
    return formatCellValue(column, item.value)
  }

  function apply() {
    if (!loaded) {
      return
    }
    const chosen = loaded.values
      .map((item) => item.value)
      .filter((value) => staged.has(value))
    // Values the applied filter holds that this list cannot show -- hidden
    // by the other conditions, or past the cut -- stay chosen: the list
    // reflects the others, so "everything visible" is not "everything",
    // and an Apply that changed nothing must not release the column.
    const kept = (active ?? []).filter(
      (value) => !loaded.values.some((item) => item.value === value),
    )
    const releases =
      chosen.length === loaded.values.length &&
      kept.length === 0 &&
      (active === null || !loaded.truncated)
    onApply(releases ? null : [...chosen, ...kept])
    setOpen(false)
  }

  const visible = loaded
    ? loaded.values.filter((item) =>
        labelFor(item).toLowerCase().includes(search.trim().toLowerCase()),
      )
    : []
  const allStaged =
    loaded !== null && loaded.values.every((item) => staged.has(item.value))

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          void openAndLoad()
        }
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Filter ${column.label}`}
          aria-pressed={active !== null}
          className={cn(
            "flex h-full shrink-0 items-center px-1 outline-none hover:bg-accent/45 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40",
            active !== null
              ? "text-primary"
              : "text-muted-foreground/50 hover:text-muted-foreground",
          )}
        >
          <Filter
            className="size-3"
            fill={active !== null ? "currentColor" : "none"}
          />
        </button>
      </PopoverTrigger>
      <PopoverContent
        aria-label={`${column.label} values`}
        className="flex max-h-96 w-64 flex-col gap-2"
      >
        <Input
          type="search"
          aria-label="Search values"
          placeholder="Search values…"
          className="h-8"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {failed ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            The values could not be read. Close and try again.
          </p>
        ) : loaded === null ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            Loading values…
          </p>
        ) : (
          <>
            <label className="flex items-center gap-2 border-b pb-1.5 text-sm">
              <input
                type="checkbox"
                aria-label="Select all"
                className="size-4 rounded border-border accent-primary"
                checked={allStaged}
                onChange={() =>
                  setStaged(
                    allStaged
                      ? new Set()
                      : new Set(loaded.values.map((item) => item.value)),
                  )
                }
              />
              <span className="font-medium">Select all</span>
            </label>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {visible.map((item, index) => (
                <label
                  key={index}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-accent/45"
                >
                  <input
                    type="checkbox"
                    aria-label={labelFor(item)}
                    className="size-4 shrink-0 rounded border-border accent-primary"
                    checked={staged.has(item.value)}
                    onChange={() =>
                      setStaged((current) => {
                        const next = new Set(current)
                        if (next.has(item.value)) {
                          next.delete(item.value)
                        } else {
                          next.add(item.value)
                        }
                        return next
                      })
                    }
                  />
                  <span className="truncate">{labelFor(item)}</span>
                </label>
              ))}
              {visible.length === 0 ? (
                <p className="py-3 text-center text-xs text-muted-foreground">
                  No values match the search.
                </p>
              ) : null}
            </div>
            {loaded.truncated ? (
              <p className="text-xs text-muted-foreground">
                Showing the first 200 values; the search above narrows this
                list only.
              </p>
            ) : null}
            <div className="flex justify-end gap-2 border-t pt-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={staged.size === 0}
                onClick={apply}
              >
                Apply
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
