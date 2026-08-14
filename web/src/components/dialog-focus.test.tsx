import { readFileSync, readdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RecordConflictReview } from "@/components/record-conflict-review"
import { ReportPreview } from "@/components/report-preview"
import type { TideApi } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationFormField,
  TidePresentationReport,
  TideReportDocument,
} from "@/lib/contracts"
import { compareRecordConflict } from "@/lib/conflicts"

afterEach(cleanup)

// Every one of these renders a control before and after the dialog. They stand
// for the page the backdrop covers: a dialog that lets Tab reach them is a
// dialog whose `aria-modal="true"` is a lie, and someone driving the keyboard
// ends up operating a form they cannot see.

describe("a modal dialog that keeps what it claims", () => {
  it("brings Tab back round instead of into the page behind it", async () => {
    const user = userEvent.setup()
    renderConflictReview()

    const dialog = screen.getByRole("dialog", {
      name: "Record changed elsewhere",
    })
    const first = within(dialog).getByRole("button", {
      name: "Use current Name",
    })
    const last = within(dialog).getByRole("button", {
      name: "Reload current",
    })

    last.focus()
    await user.tab()
    expect(first).toHaveFocus()

    await user.tab({ shift: true })
    expect(last).toHaveFocus()
  })

  it("skips the controls it has disabled", async () => {
    // Apply resolution is the last button in the markup and is disabled until
    // every overlap is decided. Wrapping onto it would put focus on a control
    // that cannot be pressed and cannot be left by tabbing forward again.
    const user = userEvent.setup()
    renderConflictReview()

    const dialog = screen.getByRole("dialog", {
      name: "Record changed elsewhere",
    })
    expect(
      within(dialog).getByRole("button", { name: "Apply resolution" }),
    ).toBeDisabled()

    within(dialog)
      .getByRole("button", { name: "Use current Name" })
      .focus()
    await user.tab({ shift: true })

    expect(
      within(dialog).getByRole("button", { name: "Reload current" }),
    ).toHaveFocus()
  })

  it("hands focus back to whatever opened it", async () => {
    // The dialog autofocuses a button of its own, so the element to return to
    // has to be read while it is still the active one -- during the first
    // render, before React commits that autofocus. Reading it from an effect
    // records the dialog's own button, which is gone by the time it is needed,
    // and focus lands on the body with the next Tab starting from the top of
    // the page.
    const user = userEvent.setup()
    render(<ConflictHarness />)

    const save = screen.getByRole("button", { name: "Save" })
    await user.click(save)
    const dialog = await screen.findByRole("dialog", {
      name: "Record changed elsewhere",
    })

    await user.click(
      within(dialog).getByRole("button", { name: "Continue editing" }),
    )

    expect(save).toHaveFocus()
  })
})

describe("a report preview that keeps what it claims", () => {
  it("takes focus off the page behind when it opens", async () => {
    const user = userEvent.setup()
    render(<ReportHarness />)

    await user.click(screen.getByRole("button", { name: "Preview" }))

    expect(
      await screen.findByRole("button", { name: "Close report preview" }),
    ).toHaveFocus()
  })

  it("brings Tab back round instead of into the page behind it", async () => {
    const user = userEvent.setup()
    render(<ReportHarness />)

    await user.click(screen.getByRole("button", { name: "Preview" }))
    const dialog = await screen.findByRole("dialog", {
      name: "Posted Sales Summary",
    })
    expect(await within(dialog).findByText("4,610.00")).toBeInTheDocument()

    within(dialog).getByRole("button", { name: "Close" }).focus()
    await user.tab()

    expect(
      within(dialog).getByRole("button", { name: "Close report preview" }),
    ).toHaveFocus()
  })
})

it("leaves no dialog to keep the promise on its own", () => {
  // The lookup dialog carried the only copy of this and was the only one that
  // worked. Three components agreeing is how the field-label transform looked
  // before one of them drifted, so the agreement is checked rather than
  // assumed -- and the fourth dialog is caught the day it is written.
  const components = dirname(fileURLToPath(import.meta.url))
  const unguarded = readdirSync(components, {
    recursive: true,
    encoding: "utf8",
  })
    .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx"))
    .filter((name) => {
      const source = readFileSync(join(components, name), "utf8")
      return (
        source.includes('role="dialog"') &&
        !source.includes("useDialogFocus")
      )
    })

  expect(unguarded).toEqual([])
})

function ConflictHarness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button">Before</button>
      <button type="button" onClick={() => setOpen(true)}>
        Save
      </button>
      {open ? (
        <ConflictDialog onContinueEditing={() => setOpen(false)} />
      ) : null}
      <button type="button">After</button>
    </>
  )
}

function ReportHarness() {
  const [open, setOpen] = useState(false)
  return (
    <QueryClientProvider client={new QueryClient()}>
      <button type="button">Before</button>
      <button type="button" onClick={() => setOpen(true)}>
        Preview
      </button>
      {open ? (
        <ReportPreview
          api={reportApi()}
          report={summary}
          identity={null}
          onClose={() => setOpen(false)}
        />
      ) : null}
      <button type="button">After</button>
    </QueryClientProvider>
  )
}

function renderConflictReview() {
  return render(
    <>
      <button type="button">Before</button>
      <ConflictDialog onContinueEditing={vi.fn()} />
      <button type="button">After</button>
    </>,
  )
}

function ConflictDialog({
  onContinueEditing,
}: {
  onContinueEditing: () => void
}) {
  return (
    <RecordConflictReview
      form={form}
      collections={[]}
      conflict={compareRecordConflict(
        { name: "Support", note: "opened" },
        { name: "Server support", note: "opened" },
        { name: "My support", note: "opened" },
        ["name", "note"],
      )}
      lockedFields={new Set()}
      choices={{}}
      onChoice={vi.fn()}
      onContinueEditing={onContinueEditing}
      onReloadCurrent={vi.fn()}
      onApply={vi.fn()}
    />
  )
}

function reportApi(): TideApi {
  return {
    buildReport: () => Promise.resolve(summaryDocument),
    exportReport: () =>
      Promise.resolve({ blob: new Blob(), filename: "summary.csv" }),
  } as unknown as TideApi
}

const nameField: TidePresentationFormField = {
  name: "name",
  label: "Name",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
  values: [],
  writable: true,
  required: false,
  help: null,
  max_length: null,
  choices: [],
  regex: null,
  numeric_mask: null,
  precision: null,
  scale: null,
  minimum: null,
  maximum: null,
  has_default: false,
  default_value: null,
}

const form: TideFormPresentation = {
  view: "catalog.Product.edit",
  entity: "catalog.Product",
  label: "Product",
  display_template: null,
  fields: { name: nameField },
  sections: [],
}

const summary: TidePresentationReport = {
  name: "sales.summary",
  title: "Posted Sales Summary",
  kind: "summary",
  entity: "sales.Invoice",
  resource_path: "/api/v1/_tide/reports/sales.summary",
  export_formats: ["csv"],
}

const summaryDocument: TideReportDocument = {
  wire_version: "0.1",
  report: "sales.summary",
  title: "Posted Sales Summary",
  application: "TIDE Invoicing",
  generated_at: "2026-07-30T12:00:00Z",
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
  suggested_filename: "sales-summary-2026-07-30",
}
