// One person's arrangement of a grid: which of the offered columns it shows,
// in what order, under what names. The chooser edits a draft and hands the
// result to the workspace -- it owns no transport, so what these tests pin
// is the contract of the draft: what starts checked, what Apply sends, and
// that a rename or a reorder changes exactly what it claims to.
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ColumnChooser } from "@/components/column-chooser"
import type {
  TideBrowsePresentation,
  TidePresentationColumn,
  TideViewStateColumn,
} from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function column(name: string, label: string): TidePresentationColumn {
  return {
    name,
    label,
    field_type: "string",
    alignment: "left",
    format: null,
    format_options: null,
    target_entity: null,
    reference: null,
    values: [],
  }
}

const VIEW = {
  view: "sales.Invoice.browse",
  entity: "sales.Invoice",
  label: "Invoices",
  resource_path: "/api/v1/invoices",
  query_path: "/api/v1/invoices/_query",
  identity_field: "id",
  columns: [column("number", "Number"), column("total", "Total")],
  available_columns: [
    column("currency", "Currency"),
    column("number", "Number"),
    column("total", "Total"),
    column("version", "Version"),
  ],
  search_field: null,
  search_label: null,
  named_filters: [],
  sortable_fields: ["number", "total", "version", "currency"],
  filterable_fields: ["number", "total", "version", "currency"],
  summaries: [],
  edit: "form",
  export_formats: [],
  page_size: 50,
  operations: ["list"],
  detail_view: null,
} as unknown as TideBrowsePresentation

async function open(
  state: TideViewStateColumn[] = [],
  onSave = vi.fn(async () => {}),
  onReset = vi.fn(async () => {}),
) {
  render(
    <ColumnChooser view={VIEW} state={state} onSave={onSave} onReset={onReset} />,
  )
  await userEvent.click(
    screen.getByRole("button", { name: "Choose columns" }),
  )
  return { onSave, onReset }
}

function shownRows(): HTMLElement[] {
  return screen.getAllByRole("listitem", { name: /shown:/i })
}

describe("the column chooser", () => {
  it("starts from the declared columns when nothing is stored", async () => {
    await open()

    expect(
      shownRows().map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Shown: Number", "Shown: Total"])
    const offered = screen.getAllByRole("checkbox")
    expect(offered.map((box) => box.getAttribute("aria-label"))).toEqual([
      "Show Currency",
      "Show Version",
    ])
  })

  it("starts from the stored arrangement when there is one", async () => {
    await open([
      { name: "total", label: "Sum" },
      { name: "version", label: null },
    ])

    expect(
      shownRows().map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Shown: Total", "Shown: Version"])
    expect(screen.getByRole("textbox", { name: "Rename Total" })).toHaveValue(
      "Sum",
    )
  })

  it("appends a checked column and sends the arrangement on apply", async () => {
    const { onSave } = await open()

    await userEvent.click(
      screen.getByRole("checkbox", { name: "Show Version" }),
    )
    expect(
      shownRows().map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Shown: Number", "Shown: Total", "Shown: Version"])

    await userEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onSave).toHaveBeenCalledWith([
      { name: "number", label: null },
      { name: "total", label: null },
      { name: "version", label: null },
    ])
  })

  it("sends a trimmed rename and null for an empty one", async () => {
    const { onSave } = await open()

    await userEvent.type(
      screen.getByRole("textbox", { name: "Rename Number" }),
      "  No.  ",
    )
    await userEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onSave).toHaveBeenCalledWith([
      { name: "number", label: "No." },
      { name: "total", label: null },
    ])
  })

  it("moves a shown column up and drops one from the arrangement", async () => {
    const { onSave } = await open()

    await userEvent.click(
      screen.getByRole("button", { name: "Move Total up" }),
    )
    expect(
      shownRows().map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Shown: Total", "Shown: Number"])

    await userEvent.click(
      within(shownRows()[1]).getByRole("button", { name: "Hide Number" }),
    )
    expect(
      shownRows().map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Shown: Total"])
    expect(
      screen.getByRole("checkbox", { name: "Show Number" }),
    ).not.toBeChecked()

    await userEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onSave).toHaveBeenCalledWith([{ name: "total", label: null }])
  })

  it("cannot apply an empty arrangement", async () => {
    await open()

    await userEvent.click(
      within(shownRows()[0]).getByRole("button", { name: "Hide Number" }),
    )
    await userEvent.click(
      within(shownRows()[0]).getByRole("button", { name: "Hide Total" }),
    )
    expect(screen.queryAllByRole("listitem", { name: /shown:/i })).toEqual([])
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
  })

  it("resets to the declared view", async () => {
    const { onReset } = await open([{ name: "total", label: null }])

    await userEvent.click(
      screen.getByRole("button", { name: "Reset to default" }),
    )
    expect(onReset).toHaveBeenCalled()
  })
})
