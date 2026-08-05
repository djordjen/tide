import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Download,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  X,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TidePresentationReport,
  TideReportDocument,
  TideReportExportFormat,
} from "@/lib/contracts"
import { useDialogFocus } from "@/lib/dialog-focus"
import { cn } from "@/lib/utils"

interface ReportPreviewProps {
  api: TideApi
  report: TidePresentationReport
  identity: unknown | null
  onClose: () => void
}

export function ReportPreview({
  api,
  report,
  identity,
  onClose,
}: ReportPreviewProps) {
  const dialogRef = useRef<HTMLElement>(null)
  const keepFocusInDialog = useDialogFocus(dialogRef)
  const [exporting, setExporting] =
    useState<TideReportExportFormat | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ["report-preview", report.name, identity],
    queryFn: ({ signal }) => api.buildReport(report, identity, signal),
    staleTime: 0,
  })

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose()
      }
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [onClose])

  async function download(exportFormat: TideReportExportFormat) {
    setExporting(exportFormat)
    setExportError(null)
    try {
      const result = await api.exportReport(
        report,
        exportFormat,
        identity,
      )
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = result.filename
      anchor.style.display = "none"
      document.body.append(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error) {
      setExportError(
        error instanceof TideApiError
          ? error.message
          : `${exportFormat.toUpperCase()} export could not be downloaded.`,
      )
    } finally {
      setExporting(null)
    }
  }

  const error =
    query.error instanceof TideApiError
      ? query.error
      : query.error
        ? new TideApiError("The report preview could not be generated.")
        : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-2 backdrop-blur-[2px] md:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <section
        ref={dialogRef}
        aria-labelledby="tide-report-preview-title"
        aria-modal="true"
        role="dialog"
        tabIndex={-1}
        className="flex h-[min(94vh,980px)] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl"
        onKeyDown={keepFocusInDialog}
      >
        <header className="flex shrink-0 items-center justify-between gap-4 border-b px-4 py-3 md:px-6">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Secured report preview
            </p>
            <h1
              id="tide-report-preview-title"
              className="truncate text-lg font-semibold"
            >
              {report.title}
            </h1>
          </div>
          <Button
            aria-label="Close report preview"
            size="icon"
            variant="ghost"
            onClick={onClose}
          >
            <X />
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto bg-muted/35 p-3 md:p-6">
          {query.isPending ? (
            <div className="flex h-full min-h-72 items-center justify-center gap-3 text-sm text-muted-foreground">
              <LoaderCircle className="size-5 animate-spin" />
              Building the secured report…
            </div>
          ) : error ? (
            <div
              role="alert"
              className="mx-auto mt-12 max-w-lg rounded-xl border border-destructive/25 bg-background p-5 text-sm text-destructive shadow-sm"
            >
              <p className="font-medium">Report preview unavailable</p>
              <p className="mt-1 text-xs leading-5">{error.message}</p>
              <Button
                className="mt-4"
                size="sm"
                variant="outline"
                onClick={() => query.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : query.data ? (
            <ReportPage document={query.data} />
          ) : null}
        </div>

        <footer className="flex shrink-0 flex-col gap-3 border-t bg-background px-4 py-3 sm:flex-row sm:items-center sm:justify-between md:px-6">
          <div className="min-w-0 text-xs text-muted-foreground">
            {exportError ? (
              <span role="alert" className="text-destructive">
                {exportError}
              </span>
            ) : query.data ? (
              <span className="truncate">
                Generated {generatedText(query.data.generated_at)}
              </span>
            ) : (
              <span>Exports become available after preview generation.</span>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {report.export_formats.map((exportFormat) => (
              <Button
                key={exportFormat}
                disabled={!query.data || exporting !== null}
                size="sm"
                variant={exportFormat === "pdf" ? "default" : "outline"}
                onClick={() => void download(exportFormat)}
              >
                {exporting === exportFormat ? (
                  <LoaderCircle className="animate-spin" />
                ) : exportFormat === "csv" ? (
                  <FileSpreadsheet />
                ) : exportFormat === "html" ? (
                  <FileText />
                ) : (
                  <Download />
                )}
                Download {exportFormat.toUpperCase()}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </footer>
      </section>
    </div>
  )
}

function ReportPage({ document }: { document: TideReportDocument }) {
  return (
    <article className="mx-auto min-h-[70rem] max-w-4xl bg-white px-6 py-8 text-slate-800 shadow-sm ring-1 ring-slate-900/8 sm:px-10 md:px-14 md:py-12">
      <header className="flex flex-col justify-between gap-5 border-b-[3px] border-blue-600 pb-5 sm:flex-row">
        <div>
          <h2 className="text-3xl font-semibold tracking-tight text-blue-700">
            {document.title}
          </h2>
          <p className="mt-1 text-sm font-semibold">{document.application}</p>
        </div>
        <p className="text-xs leading-5 text-slate-500 sm:text-right">
          Generated
          <br />
          {generatedText(document.generated_at)}
        </p>
      </header>

      {document.header_text.length > 0 ? (
        <div className="mt-5 space-y-1 text-sm text-slate-600">
          {document.header_text.map((text, index) => (
            <p key={`${text}-${index}`}>{text}</p>
          ))}
        </div>
      ) : null}

      {document.record_values.length > 0 ? (
        <dl className="mt-7 grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          {document.record_values.map((value, index) => (
            <div
              key={`${value.label}-${index}`}
              className="grid grid-cols-[8rem_1fr] gap-2"
            >
              <dt className="font-semibold text-slate-500">{value.label}</dt>
              <dd className={alignmentClass(value.alignment)}>{value.text}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="mt-8 overflow-x-auto">
        <table className="w-full min-w-max border-collapse text-xs sm:text-sm">
          <thead>
            <tr className="bg-slate-800 text-white">
              {document.detail.columns.map((column) => (
                <th
                  key={column.name}
                  className={cn(
                    "px-3 py-2.5 font-semibold",
                    alignmentClass(column.alignment),
                  )}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {document.detail.rows.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className="border-b border-slate-200 even:bg-slate-50"
              >
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className={cn(
                      "px-3 py-2.5",
                      alignmentClass(cell.alignment),
                    )}
                  >
                    {cell.text}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {document.footer_values.length > 0 ? (
        <dl className="mt-7 ml-auto w-full max-w-sm border-t-2 border-blue-600 pt-3 text-sm">
          {document.footer_values.map((value, index) => (
            <div
              key={`${value.label}-${index}`}
              className="grid grid-cols-2 gap-3 py-1 last:text-base last:font-semibold"
            >
              <dt>{value.label}</dt>
              <dd className={alignmentClass(value.alignment)}>{value.text}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <footer className="mt-12 border-t border-slate-300 pt-2 text-center text-xs text-slate-500">
        {document.page_footer_template
          .replace("{page_number}", "1")
          .replace("{page_count}", "1")}
      </footer>
    </article>
  )
}

function alignmentClass(alignment: string): string {
  return alignment === "right"
    ? "text-right tabular-nums"
    : alignment === "center"
      ? "text-center"
      : "text-left"
}

function generatedText(value: string): string {
  const generated = new Date(value)
  return Number.isNaN(generated.getTime())
    ? value
    : generated.toLocaleString()
}
