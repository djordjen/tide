// Taking the grid away. The control sends the conditions on screen rather
// than the rows that happen to be loaded -- the grid is virtualised, so what
// is loaded is not the table -- and it never offers a format the manifest did
// not, because the manifest already filtered by capability and by what the
// server can write.
//
// A capped export still arrives, and the reader is told what they got. A file
// that quietly stops at ten thousand rows is worse than no file.
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { BrowseExportControl } from "@/components/browse-export-control"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowseDownload,
  TideBrowseExportFormat,
  TideBrowsePresentation,
  TideFilterInput,
} from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

beforeEach(() => {
  const NativeURL = URL
  vi.stubGlobal(
    "URL",
    class extends NativeURL {
      static createObjectURL() {
        return "blob:tide-export"
      }

      static revokeObjectURL() {}
    },
  )
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
})

const FILTERS: TideFilterInput[] = [
  { field: "status", operator: "eq", value: "draft" },
]

describe("the browse export control", () => {
  it("offers nothing when the manifest offers no format", () => {
    renderControl([])

    expect(screen.queryByRole("button", { name: /export/i })).toBeNull()
  })

  it("sends the conditions on screen and downloads what comes back", async () => {
    const calls: unknown[][] = []
    renderControl(["csv"], download({ rows: 2, total: 2 }), calls)

    await userEvent.click(
      screen.getByRole("button", { name: "Export records" }),
    )

    await waitFor(() => expect(calls).toHaveLength(1))
    const [, format, filters, sort] = calls[0]
    expect(format).toBe("csv")
    expect(filters).toEqual(FILTERS)
    expect(sort).toEqual([{ field: "number", descending: false }])
  })

  it("says what it got before the reader trusts a capped file", async () => {
    renderControl(["csv"], download({ rows: 10000, total: 48231 }))

    await userEvent.click(
      screen.getByRole("button", { name: "Export records" }),
    )

    const notice = await screen.findByRole("status")
    expect(notice.textContent).toContain("10,000")
    expect(notice.textContent).toContain("48,231")
  })

  it("stays quiet when the file is the whole answer", async () => {
    renderControl(["csv"], download({ rows: 9, total: 9 }))

    await userEvent.click(
      screen.getByRole("button", { name: "Export records" }),
    )

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Export records" }),
      ).toBeEnabled(),
    )
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("offers each format when the server can write more than one", async () => {
    const calls: unknown[][] = []
    renderControl(["csv", "xlsx"], download({ rows: 2, total: 2 }), calls)

    await userEvent.click(
      screen.getByRole("button", { name: "Export records" }),
    )
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Excel workbook" }),
    )

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0][1]).toBe("xlsx")
  })

  it("reports a refusal rather than handing over an empty file", async () => {
    renderControl(["csv"], () => Promise.reject(new Error("nope")))

    await userEvent.click(
      screen.getByRole("button", { name: "Export records" }),
    )

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("CSV")
  })
})

function download(
  counts: Pick<TideBrowseDownload, "rows" | "total">,
): () => Promise<TideBrowseDownload> {
  return () =>
    Promise.resolve({
      blob: new Blob(["a,b\n1,2\n"], { type: "text/csv" }),
      filename: "invoices-2026-08-22.csv",
      ...counts,
    })
}

function renderControl(
  formats: TideBrowseExportFormat[],
  exporting: () => Promise<TideBrowseDownload> = download({
    rows: 1,
    total: 1,
  }),
  calls: unknown[][] = [],
) {
  const api = {
    exportBrowse: (...args: unknown[]) => {
      calls.push(args)
      return exporting()
    },
  } as unknown as TideApi
  return render(
    <BrowseExportControl
      api={api}
      view={view(formats)}
      filters={FILTERS}
      sort={[{ field: "number", descending: false }]}
    />,
  )
}

function view(formats: TideBrowseExportFormat[]): TideBrowsePresentation {
  return {
    view: "sales.Invoice.browse",
    entity: "sales.Invoice",
    label: "Invoices",
    resource_path: "/api/v1/invoices",
    query_path: "/api/v1/invoices/_query",
    identity_field: "id",
    columns: [],
    search_field: null,
    search_label: null,
    named_filters: [],
    sortable_fields: [],
    export_formats: formats,
    page_size: 50,
    operations: ["list"],
    detail_view: null,
  }
}
