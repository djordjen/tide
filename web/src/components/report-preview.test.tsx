// A grouped report renders as bands inside the one table: each group heads
// its own slice of rows and closes with its subtotal, and a flat document
// renders exactly as it always did. The band rows are full-width cells, so
// the columns keep their shared widths across every group.
//
// A parameterized report renders a form from the manifest's parameter
// metadata. The inputs collect strings and nothing else -- typing and the
// required check belong to the report service -- and a blank input is simply
// not sent, so an all-optional form left blank builds the report `{}` always
// built.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ReportPreview } from "@/components/report-preview"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationReport,
  TideReportDocument,
} from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("a grouped report preview", () => {
  it("bands each group with its heading, rows and subtotal", async () => {
    renderPreview(groupedDocument)

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    const headings = await within(dialog).findAllByTestId(
      "report-group-heading",
    )
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Customer: ADRIA - Adria Consulting  ·  Currency: EUR",
      "Customer: MORA - Mora Trade  ·  Currency: EUR",
    ])
    const footers = within(dialog).getAllByTestId("report-group-footer")
    expect(footers.map((footer) => footer.textContent)).toEqual([
      "Invoices: 2  ·  Sales total: 2,210.00",
      "Invoices: 1  ·  Sales total: 850.00",
    ])
    // The slices carry the right rows: MORA's invoice sits after its heading.
    expect(within(dialog).getByText("INV-2026-0099")).toBeInTheDocument()
    // And the grand total still closes the document.
    expect(within(dialog).getByText("3,060.00")).toBeInTheDocument()
  })

  it("renders a flat document without any band rows", async () => {
    renderPreview(flatDocument)

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    expect(await within(dialog).findByText("4,610.00")).toBeInTheDocument()
    expect(
      within(dialog).queryAllByTestId("report-group-heading"),
    ).toEqual([])
    expect(within(dialog).queryAllByTestId("report-group-footer")).toEqual([])
  })
})

describe("a parameterized report preview", () => {
  it("builds an all-optional report immediately, then narrows it on submit", async () => {
    const built: Record<string, string>[] = []
    renderPreviewWith(recordingApi(groupedDocument, built), optionalSummary)

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    // Every parameter is optional, so the first build waits for nobody:
    // the report a blank form describes is the report `{}` always built.
    expect(await within(dialog).findByText("INV-2026-0099")).toBeInTheDocument()
    expect(built).toEqual([{}])

    fireEvent.change(within(dialog).getByLabelText(/From Date/), {
      target: { value: "2026-08-01" },
    })
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Build report" }),
    )

    await waitFor(() => expect(built).toHaveLength(2))
    // The filled input travels as a string; the blank one is not sent.
    expect(built[1]).toEqual({ from_date: "2026-08-01" })
  })

  it("collects dates through native date inputs", async () => {
    renderPreviewWith(
      recordingApi(groupedDocument, []),
      optionalSummary,
    )

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    expect(within(dialog).getByLabelText(/From Date/)).toHaveAttribute(
      "type",
      "date",
    )
  })

  it("waits for required parameters instead of building a refusal", async () => {
    const built: Record<string, string>[] = []
    renderPreviewWith(recordingApi(groupedDocument, built), requiredSummary)

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    // Building `{}` would only render the service's refusal, so nothing is
    // requested until the required value is supplied.
    expect(built).toEqual([])
    expect(
      within(dialog).getByTestId("report-parameters-prompt"),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByText(/From Date\s*\(required\)/),
    ).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText(/From Date/), {
      target: { value: "2026-08-01" },
    })
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Build report" }),
    )

    await waitFor(() => expect(built).toEqual([{ from_date: "2026-08-01" }]))
    expect(await within(dialog).findByText("INV-2026-0099")).toBeInTheDocument()
  })

  it("exports the report the preview was built with, not unsubmitted edits", async () => {
    const NativeURL = URL
    vi.stubGlobal(
      "URL",
      class extends NativeURL {
        static createObjectURL() {
          return "blob:tide-report"
        }

        static revokeObjectURL() {}
      },
    )
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      () => {},
    )
    const built: Record<string, string>[] = []
    const exported: Record<string, string>[] = []
    renderPreviewWith(
      recordingApi(groupedDocument, built, exported),
      optionalSummary,
    )

    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    fireEvent.change(within(dialog).getByLabelText(/From Date/), {
      target: { value: "2026-08-01" },
    })
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Build report" }),
    )
    await waitFor(() => expect(built).toHaveLength(2))

    // An edit the user has not built yet must not leak into the download:
    // the exported file has to match the preview on screen.
    fireEvent.change(within(dialog).getByLabelText(/From Date/), {
      target: { value: "2026-12-31" },
    })
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Download CSV" }),
    )

    await waitFor(() => expect(exported).toEqual([{ from_date: "2026-08-01" }]))
  })
})

function renderPreview(document: TideReportDocument) {
  return renderPreviewWith(reportApi(document), summary)
}

function renderPreviewWith(api: TideApi, report: TidePresentationReport) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ReportPreview
        api={api}
        report={report}
        identity={null}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

function reportApi(document: TideReportDocument): TideApi {
  return {
    buildReport: () => Promise.resolve(document),
    exportReport: () =>
      Promise.resolve({ blob: new Blob(), filename: "summary.csv" }),
  } as unknown as TideApi
}

function recordingApi(
  document: TideReportDocument,
  built: Record<string, string>[],
  exported: Record<string, string>[] = [],
): TideApi {
  return {
    buildReport: (
      _report: TidePresentationReport,
      _identity: unknown,
      parameters: Record<string, string>,
    ) => {
      built.push(parameters)
      return Promise.resolve(document)
    },
    exportReport: (
      _report: TidePresentationReport,
      _format: string,
      _identity: unknown,
      parameters: Record<string, string>,
    ) => {
      exported.push(parameters)
      return Promise.resolve({ blob: new Blob(), filename: "summary.csv" })
    },
  } as unknown as TideApi
}

const summary: TidePresentationReport = {
  name: "sales.summary",
  title: "Posted Sales Summary",
  kind: "summary",
  entity: "sales.Invoice",
  resource_path: "/api/v1/_tide/reports/sales.summary",
  export_formats: ["csv"],
}

const optionalSummary: TidePresentationReport = {
  ...summary,
  parameters: [
    { name: "from_date", label: "From Date", type: "date", required: false },
    { name: "to_date", label: "To Date", type: "date", required: false },
  ],
}

const requiredSummary: TidePresentationReport = {
  ...summary,
  parameters: [
    { name: "from_date", label: "From Date", type: "date", required: true },
  ],
}

const groupedDocument: TideReportDocument = {
  wire_version: "0.1",
  report: "sales.summary",
  title: "Posted Sales Summary",
  application: "TIDE Invoicing",
  generated_at: "2026-08-16T12:00:00Z",
  header_text: [],
  record_values: [],
  detail: {
    columns: [
      { name: "number", label: "Number", alignment: "left" },
      { name: "total", label: "Total", alignment: "right" },
    ],
    rows: [
      [
        { text: "INV-2026-0001", alignment: "left" },
        { text: "850.00", alignment: "right" },
      ],
      [
        { text: "INV-2026-0004", alignment: "left" },
        { text: "1,360.00", alignment: "right" },
      ],
      [
        { text: "INV-2026-0099", alignment: "left" },
        { text: "850.00", alignment: "right" },
      ],
    ],
  },
  footer_values: [
    { label: "Invoices", text: "3", alignment: "right" },
    { label: "Sales total", text: "3,060.00", alignment: "right" },
  ],
  page_footer_template: "Page {page_number} of {page_count}",
  suggested_filename: "posted-sales-summary-2026-08-16",
  groups: [
    {
      values: [
        {
          label: "Customer",
          text: "ADRIA - Adria Consulting",
          alignment: "left",
        },
        { label: "Currency", text: "EUR", alignment: "left" },
      ],
      row_start: 0,
      row_count: 2,
      footer_values: [
        { label: "Invoices", text: "2", alignment: "right" },
        { label: "Sales total", text: "2,210.00", alignment: "right" },
      ],
    },
    {
      values: [
        { label: "Customer", text: "MORA - Mora Trade", alignment: "left" },
        { label: "Currency", text: "EUR", alignment: "left" },
      ],
      row_start: 2,
      row_count: 1,
      footer_values: [
        { label: "Invoices", text: "1", alignment: "right" },
        { label: "Sales total", text: "850.00", alignment: "right" },
      ],
    },
  ],
}

const flatDocument: TideReportDocument = {
  wire_version: "0.1",
  report: "sales.summary",
  title: "Posted Sales Summary",
  application: "TIDE Invoicing",
  generated_at: "2026-08-16T12:00:00Z",
  header_text: [],
  record_values: [],
  detail: {
    columns: [
      { name: "customer", label: "Customer", alignment: "left" },
      { name: "sales_total", label: "Sales total", alignment: "right" },
    ],
    rows: [
      [
        { text: "ADRIA - Adria Consulting", alignment: "left" },
        { text: "4,610.00", alignment: "right" },
      ],
    ],
  },
  footer_values: [],
  page_footer_template: "Page {page_number} of {page_count}",
  suggested_filename: "posted-sales-summary-2026-08-16",
}
