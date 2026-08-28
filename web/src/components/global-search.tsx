import { useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { LoaderCircle, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { TideApiError, type TideApi } from "@/lib/api"
import type { TidePresentationManifest } from "@/lib/contracts"
import {
  entityRecordHref,
  referenceLinkClick,
} from "@/lib/reference-link"

/**
 * One box over everything: the server sweeps every searchable entity this
 * identity may read and answers bounded, grouped hits in model order.
 *
 * A hit is a doorway, so it behaves exactly like a reference link -- the
 * same href, the same in-place follow, the same Close walking back -- and a
 * group whose entity has no view in the manifest is withheld entirely,
 * because a result that cannot be opened is not a result on this surface.
 */
export function GlobalSearch({
  api,
  views,
}: {
  api: TideApi
  views: TidePresentationManifest["views"]
}) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const candidate = useDebouncedValue(text.trim(), 300)
  const query = useQuery({
    queryKey: ["global-search", candidate],
    queryFn: ({ signal }) => api.searchEverywhere(candidate, signal),
    enabled: open && candidate.length > 0,
    staleTime: 15_000,
    placeholderData: keepPreviousData,
  })
  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The search could not be run.")
        : null
  const groups = (query.data?.groups ?? [])
    .map((group) => ({
      ...group,
      records: group.records
        .map((hit) => ({
          ...hit,
          href: entityRecordHref(views, group.entity, hit.identity),
        }))
        .filter(
          (hit): hit is typeof hit & { href: string } => hit.href !== null,
        ),
    }))
    .filter((group) => group.records.length > 0)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              aria-label="Search everywhere"
              size="icon"
              variant="ghost"
            >
              <Search />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Search everywhere</TooltipContent>
      </Tooltip>
      <PopoverContent
        align="end"
        aria-label="Search everywhere"
        className="flex max-h-[26rem] w-80 flex-col gap-2"
      >
        <div className="relative">
          <Input
            autoFocus
            type="search"
            aria-label="Search everywhere"
            placeholder="Search everywhere…"
            className="h-8 pr-8"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          {query.isFetching ? (
            <LoaderCircle className="absolute top-2 right-2.5 size-4 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        {error ? (
          <p role="alert" className="py-3 text-center text-xs text-destructive">
            {error.message}
          </p>
        ) : candidate.length === 0 ? null : query.isPending ? null : groups.length ===
          0 ? (
          <p className="py-3 text-center text-xs text-muted-foreground">
            Nothing matches this search.
          </p>
        ) : (
          <div className="min-h-0 overflow-y-auto">
            {groups.map((group) => (
              <section key={group.entity} className="mb-1 last:mb-0">
                <div className="flex items-baseline justify-between gap-2 border-b px-1 pt-1.5 pb-1">
                  <h3 className="text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
                    {group.label}
                  </h3>
                  {group.truncated ? (
                    <span className="text-[0.65rem] text-muted-foreground/70">
                      first matches only
                    </span>
                  ) : null}
                </div>
                <ul>
                  {group.records.map((hit) => (
                    <li key={`${group.entity}-${String(hit.identity)}`}>
                      <a
                        href={hit.href}
                        className="block rounded-md px-2 py-1.5 text-sm outline-none hover:bg-accent/45 focus-visible:ring-2 focus-visible:ring-ring/40"
                        onClick={(event) => {
                          referenceLinkClick(event, hit.href)
                          if (event.defaultPrevented) {
                            setOpen(false)
                          }
                        }}
                      >
                        {hit.display}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
