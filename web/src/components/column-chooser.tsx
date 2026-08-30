// One person's arrangement of a grid.
//
// The manifest's available_columns is the whole offer -- every readable,
// non-collection field -- and the declared columns are the default every
// principal starts from. What this edits is the overlay between them:
// which offered columns to show, in what order, under what names. The
// draft lives here; the transport lives in the workspace, so Apply and
// Reset are handed out as callbacks and the popover closes when they keep.
import { Check, ChevronDown, ChevronUp, Columns3, X } from "lucide-react"
import { useState, type ReactElement } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type {
  TideBrowsePresentation,
  TideViewStateColumn,
} from "@/lib/contracts"

interface DraftColumn {
  name: string
  label: string
}

function draftFrom(
  view: TideBrowsePresentation,
  state: TideViewStateColumn[],
): DraftColumn[] {
  if (state.length) {
    return state.map((column) => ({
      name: column.name,
      label: column.label ?? "",
    }))
  }
  return view.columns.map((column) => ({ name: column.name, label: "" }))
}

export function ColumnChooser({
  view,
  state,
  onSave,
  onReset,
}: {
  view: TideBrowsePresentation
  state: TideViewStateColumn[]
  onSave: (columns: TideViewStateColumn[]) => Promise<void>
  onReset: () => Promise<void>
}): ReactElement | null {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<DraftColumn[]>([])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const offer = view.available_columns ?? []

  if (!offer.length) {
    return null
  }
  const byName = new Map(offer.map((column) => [column.name, column]))

  function declaredLabel(name: string): string {
    return byName.get(name)?.label ?? name
  }

  function openWithDraft(next: boolean) {
    if (next) {
      setDraft(draftFrom(view, state).filter((item) => byName.has(item.name)))
      setFailure(null)
    }
    setOpen(next)
  }

  function move(index: number, delta: number) {
    setDraft((current) => {
      const target = index + delta
      if (target < 0 || target >= current.length) {
        return current
      }
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  async function run(work: () => Promise<void>) {
    setBusy(true)
    setFailure(null)
    try {
      await work()
      setOpen(false)
    } catch (error) {
      setFailure(
        error instanceof Error
          ? error.message
          : "The arrangement could not be kept.",
      )
    } finally {
      setBusy(false)
    }
  }

  const shownNames = new Set(draft.map((item) => item.name))
  const hidden = offer.filter((column) => !shownNames.has(column.name))

  return (
    <Popover open={open} onOpenChange={openWithDraft}>
      <PopoverTrigger asChild>
        <Button aria-label="Choose columns" size="icon" variant="outline">
          <Columns3 />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <div className="mb-2 text-sm font-medium">Columns</div>
        <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
          {draft.map((item, index) => (
            <li
              key={item.name}
              aria-label={`Shown: ${declaredLabel(item.name)}`}
              className="flex items-center gap-1"
            >
              <Button
                aria-label={`Hide ${declaredLabel(item.name)}`}
                title={`Hide ${declaredLabel(item.name)}`}
                size="icon"
                variant="ghost"
                className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
                onClick={() =>
                  setDraft((current) =>
                    current.filter((entry) => entry.name !== item.name),
                  )
                }
              >
                <X />
              </Button>
              <Input
                aria-label={`Rename ${declaredLabel(item.name)}`}
                className="h-7 flex-1 text-sm"
                placeholder={declaredLabel(item.name)}
                value={item.label}
                onChange={(event) =>
                  setDraft((current) =>
                    current.map((entry) =>
                      entry.name === item.name
                        ? { ...entry, label: event.target.value }
                        : entry,
                    ),
                  )
                }
              />
              <Button
                aria-label={`Move ${declaredLabel(item.name)} up`}
                title={`Move ${declaredLabel(item.name)} up`}
                size="icon"
                variant="ghost"
                className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                <ChevronUp />
              </Button>
              <Button
                aria-label={`Move ${declaredLabel(item.name)} down`}
                title={`Move ${declaredLabel(item.name)} down`}
                size="icon"
                variant="ghost"
                className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
                disabled={index === draft.length - 1}
                onClick={() => move(index, 1)}
              >
                <ChevronDown />
              </Button>
            </li>
          ))}
        </ul>
        {hidden.length ? (
          <>
            <div className="mb-1 mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Available
            </div>
            <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto">
              {hidden.map((column) => (
                <li key={column.name} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Show ${column.label}`}
                    className="size-4 accent-primary"
                    checked={false}
                    onChange={() =>
                      setDraft((current) => [
                        ...current,
                        { name: column.name, label: "" },
                      ])
                    }
                  />
                  <span className="truncate text-sm">{column.label}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {failure ? (
          <div role="alert" className="mt-2 text-xs text-destructive">
            {failure}
          </div>
        ) : null}
        <div className="mt-3 flex items-center justify-between gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => run(onReset)}
          >
            Reset to default
          </Button>
          <Button
            size="sm"
            disabled={busy || !draft.length}
            onClick={() =>
              run(() =>
                onSave(
                  draft.map((item) => ({
                    name: item.name,
                    label: item.label.trim() || null,
                  })),
                ),
              )
            }
          >
            <Check />
            Apply
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
