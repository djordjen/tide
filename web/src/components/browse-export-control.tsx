// Taking the grid away.
//
// The control sends the conditions the reader is looking at, never the rows
// that happen to be loaded: the grid is virtualised and fetches as it
// scrolls, so what is in memory is a page, not the table. The server walks
// the whole filtered set under the same security and answers with a file.
//
// It offers exactly what the manifest offered, which is already filtered
// twice -- by whether this principal holds the export capability, and by
// whether the server has a writer for the format. So there is no format
// check to make here beyond trusting that list.
import { Download } from "lucide-react"
import { useState, type ReactElement } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideBrowseExportFormat,
  TideBrowsePresentation,
  TideFilterInput,
  TideSortInput,
} from "@/lib/contracts"

const FORMAT_LABELS: Record<TideBrowseExportFormat, string> = {
  csv: "CSV",
  xlsx: "Excel workbook",
}

export function BrowseExportControl({
  api,
  view,
  filters,
  sort,
}: {
  api: TideApi
  view: TideBrowsePresentation
  filters: TideFilterInput[]
  sort: TideSortInput[]
}): ReactElement | null {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const formats = view.export_formats ?? []

  if (!formats.length) {
    return null
  }

  async function run(exportFormat: TideBrowseExportFormat) {
    setBusy(true)
    setNotice(null)
    setFailure(null)
    try {
      const file = await api.exportBrowse(view, exportFormat, filters, sort)
      save(file.blob, file.filename)
      if (file.rows < file.total) {
        // The cap stopped the walk. Saying so is the whole reason the server
        // sends both numbers: a file that quietly ends at ten thousand rows
        // reads as the answer.
        setNotice(
          `Exported the first ${file.rows.toLocaleString()} of ` +
            `${file.total.toLocaleString()} rows. Narrow the filter to take ` +
            `the rest.`,
        )
      }
    } catch (error) {
      setFailure(
        error instanceof TideApiError
          ? error.message
          : `${exportFormat.toUpperCase()} export could not be downloaded.`,
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {formats.length === 1 ? (
        <Button
          aria-label="Export records"
          variant="outline"
          disabled={busy}
          onClick={() => void run(formats[0])}
        >
          <Download />
          Export
        </Button>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button aria-label="Export records" variant="outline" disabled={busy}>
              <Download />
              Export
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Export these records</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {formats.map((exportFormat) => (
              <DropdownMenuItem
                key={exportFormat}
                onSelect={() => void run(exportFormat)}
              >
                {FORMAT_LABELS[exportFormat]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {notice ? (
        <span
          role="status"
          className="text-xs text-muted-foreground"
        >
          {notice}
        </span>
      ) : null}
      {failure ? (
        <span role="alert" className="text-xs text-destructive">
          {failure}
        </span>
      ) : null}
    </>
  )
}

function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.style.display = "none"
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
