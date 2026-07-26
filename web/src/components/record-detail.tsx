import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import {
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  LockKeyhole,
  Rows3,
  ShieldCheck,
  X,
} from "lucide-react"

import { TideDisplayValue } from "@/components/tide-display-value"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormGroup,
  TidePresentationFormSection,
  TideRecord,
} from "@/lib/contracts"
import { formatRecordDisplay } from "@/lib/format"
import { cn } from "@/lib/utils"

interface RecordDetailProps {
  api: TideApi
  view: TideBrowsePresentation
  form: TideFormPresentation
  identity: unknown
  position: number
  loadedCount: number
  canPrevious: boolean
  canNext: boolean
  navigationPending: boolean
  onPrevious: () => void
  onNext: () => void
  onClose: () => void
}

export function RecordDetail({
  api,
  view,
  form,
  identity,
  position,
  loadedCount,
  canPrevious,
  canNext,
  navigationPending,
  onPrevious,
  onNext,
  onClose,
}: RecordDetailProps) {
  const query = useQuery({
    queryKey: ["record-detail", view.view, identity],
    queryFn: ({ signal }) => api.getRecord(view, identity, signal),
    staleTime: 15_000,
    placeholderData: keepPreviousData,
  })
  const tabs = useMemo(() => formTabs(form.sections), [form.sections])
  const [selectedTab, setSelectedTab] = useState(tabs[0] ?? "")
  const record = query.data
  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The record could not be loaded.")
        : null

  useEffect(() => {
    if (!tabs.includes(selectedTab)) {
      setSelectedTab(tabs[0] ?? "")
    }
  }, [selectedTab, tabs])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "PageUp" && canPrevious && !navigationPending) {
        event.preventDefault()
        onPrevious()
      } else if (
        event.key === "PageDown" &&
        canNext &&
        !navigationPending
      ) {
        event.preventDefault()
        onNext()
      } else if (event.key === "Escape") {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [
    canNext,
    canPrevious,
    navigationPending,
    onClose,
    onNext,
    onPrevious,
  ])

  const visibleSections =
    tabs.length > 0
      ? form.sections.filter(
          (section) => (section.tab ?? "General") === selectedTab,
        )
      : form.sections
  const display = record
    ? formatRecordDisplay(
        form.display_template,
        record,
        view.identity_field,
      )
    : String(identity)
  const writable = new Set(record?._tide?.writable_fields ?? [])

  return (
    <main className="flex min-h-0 flex-1 flex-col p-4 md:p-6">
      <header className="mb-4 flex shrink-0 items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {form.label} — {display || String(identity)}
            </h1>
            <Badge variant="outline">Secured detail</Badge>
            {query.isFetching ? (
              <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
            ) : null}
          </div>
          <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            Record {position + 1} of {loadedCount} loaded in the current query
          </p>
        </div>
        <Button
          aria-label="Close record"
          className="shrink-0 md:hidden"
          size="icon"
          variant="ghost"
          onClick={onClose}
        >
          <X />
        </Button>
      </header>

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

      {tabs.length > 0 ? (
        <div
          className="mb-3 flex shrink-0 gap-1 overflow-x-auto rounded-xl border bg-card p-1"
          role="tablist"
          aria-label={`${form.label} sections`}
        >
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={tab === selectedTab}
              className={cn(
                "rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                tab === selectedTab
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              onClick={() => setSelectedTab(tab)}
            >
              {tab}
            </button>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border bg-card shadow-sm">
        {!record && query.isPending ? (
          <DetailSkeleton />
        ) : record ? (
          <div className="space-y-5 p-4 md:p-5">
            {visibleSections.map((section, index) =>
              section.kind === "group" ? (
                <DetailGroup
                  key={`group-${index}-${section.label}`}
                  api={api}
                  form={form}
                  record={record}
                  section={section}
                  writable={writable}
                />
              ) : (
                <DetailCollection
                  key={`collection-${section.name}`}
                  api={api}
                  record={record}
                  section={section}
                />
              ),
            )}
          </div>
        ) : null}
      </div>

      <footer className="mt-4 flex shrink-0 items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            disabled={!canPrevious || navigationPending}
            onClick={onPrevious}
          >
            <ChevronLeft />
            Previous
          </Button>
          <Button
            variant="outline"
            disabled={!canNext || navigationPending}
            onClick={onNext}
          >
            Next
            <ChevronRight />
          </Button>
          <span className="hidden text-xs text-muted-foreground xl:inline">
            Page Up / Page Down
          </span>
        </div>
        <Button className="hidden md:inline-flex" onClick={onClose}>
          Close
        </Button>
      </footer>
    </main>
  )
}

function DetailGroup({
  api,
  form,
  record,
  section,
  writable,
}: {
  api: TideApi
  form: TideFormPresentation
  record: TideRecord
  section: TidePresentationFormGroup
  writable: ReadonlySet<string>
}) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">{section.label}</h2>
      <div className="space-y-3">
        {section.rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className="tide-form-row grid gap-3"
            style={
              {
                "--tide-form-columns": row.length,
              } as CSSProperties
            }
          >
            {row.map((name) => {
              const field = form.fields[name]
              const fieldWritable = writable.has(name)
              return (
                <div
                  key={name}
                  className={cn(
                    "min-w-0 rounded-xl border px-3.5 py-3",
                    fieldWritable
                      ? "bg-background"
                      : "border-border/75 bg-muted/35",
                  )}
                >
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium text-muted-foreground">
                      {field.label}
                    </span>
                    {!fieldWritable ? (
                      <LockKeyhole
                        className="size-3 text-muted-foreground/55"
                        aria-label={`${field.label} is read-only`}
                      />
                    ) : null}
                  </div>
                  <TideDisplayValue
                    api={api}
                    column={field}
                    record={record}
                    wrap
                    className={cn(
                      "min-h-5 text-sm",
                      field.alignment === "right" &&
                        "text-right tabular-nums",
                    )}
                  />
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

function DetailCollection({
  api,
  record,
  section,
}: {
  api: TideApi
  record: TideRecord
  section: TidePresentationFormCollection
}) {
  const protectedFields = record._tide?.protected_fields ?? []
  const protectedCollection = protectedFields.includes(section.name)
  const raw = record[section.name]
  const rows = Array.isArray(raw) ? (raw as TideRecord[]) : []

  return (
    <section className="min-w-0">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{section.label}</h2>
        <Badge variant="outline">
          {protectedCollection ? "Protected" : `${rows.length} rows`}
        </Badge>
      </div>
      <div className="max-h-80 min-h-44 overflow-auto rounded-xl border">
        {protectedCollection ? (
          <div className="flex min-h-44 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LockKeyhole className="size-4" />
            This collection is protected for the current identity.
          </div>
        ) : rows.length === 0 ? (
          <div className="flex min-h-44 flex-col items-center justify-center text-sm text-muted-foreground">
            <Rows3 className="mb-2 size-5" />
            No collection records
          </div>
        ) : (
          <table className="w-full min-w-max border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
              <tr>
                {section.columns.map((column) => (
                  <th
                    key={column.name}
                    className={cn(
                      "border-r border-b px-3 py-2.5 text-xs font-semibold whitespace-nowrap last:border-r-0",
                      column.alignment === "right"
                        ? "text-right"
                        : column.alignment === "center"
                          ? "text-center"
                          : "text-left",
                    )}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr
                  key={String(row[section.columns[0]?.name] ?? rowIndex)}
                  className="border-b last:border-b-0 hover:bg-accent/25"
                >
                  {section.columns.map((column) => (
                    <td
                      key={column.name}
                      className={cn(
                        "max-w-96 border-r px-3 py-2.5 last:border-r-0",
                        column.alignment === "right"
                          ? "text-right tabular-nums"
                          : column.alignment === "center"
                            ? "text-center"
                            : "text-left",
                      )}
                    >
                      <TideDisplayValue
                        api={api}
                        column={column}
                        record={row}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function DetailSkeleton() {
  return (
    <div className="space-y-5 p-5">
      <div>
        <Skeleton className="mb-3 h-4 w-24" />
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-17 rounded-xl" />
          ))}
        </div>
      </div>
      <Skeleton className="h-52 rounded-xl" />
    </div>
  )
}

function formTabs(sections: TidePresentationFormSection[]): string[] {
  if (!sections.some((section) => section.tab)) {
    return []
  }
  return [
    ...new Set(sections.map((section) => section.tab ?? "General")),
  ]
}
