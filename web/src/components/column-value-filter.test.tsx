// One column's funnel, now speaking three kinds: the checklist for
// enumerable columns (covered end to end by App.column-filters), a
// From/To range for dates and numbers, and a contains mode for text.
// What these tests pin: the mode a field type gets, that range and
// contains never fetch distinct values, that Apply hands over exactly
// one discriminated state, and that emptying every input releases the
// column.
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ColumnValueFilter } from "@/components/column-value-filter"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TidePresentationColumn,
} from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const VIEW = { view: "sales.Invoice.browse" } as TideBrowsePresentation

// Range and contains must never ask the server for a value list.
const NEVER_FETCH = {
  distinct: vi.fn(async () => {
    throw new Error("distinct must not be fetched for this mode")
  }),
} as unknown as TideApi

function column(
  name: string,
  label: string,
  fieldType: string,
): TidePresentationColumn {
  return {
    name,
    label,
    field_type: fieldType,
    alignment: "left",
    format: null,
    format_options: null,
    target_entity: null,
    reference: null,
    values: [],
  } as unknown as TidePresentationColumn
}

describe("a date column's funnel", () => {
  it("offers a From/To pair and applies the staged range", async () => {
    const user = userEvent.setup()
    const onApply = vi.fn()
    render(
      <ColumnValueFilter
        api={NEVER_FETCH}
        view={VIEW}
        column={column("invoice_date", "Invoice Date", "date")}
        active={null}
        otherConditions={[]}
        onApply={onApply}
      />,
    )

    await user.click(
      screen.getByRole("button", { name: "Filter Invoice Date" }),
    )
    await user.type(screen.getByLabelText("From"), "2026-07-04")
    await user.type(screen.getByLabelText("To"), "2026-07-12")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledWith({
      kind: "range",
      from: "2026-07-04",
      to: "2026-07-12",
    })
  })

  it("relights an applied range and releases when both sides are cleared", async () => {
    const user = userEvent.setup()
    const onApply = vi.fn()
    render(
      <ColumnValueFilter
        api={NEVER_FETCH}
        view={VIEW}
        column={column("invoice_date", "Invoice Date", "date")}
        active={{ kind: "range", from: "2026-07-04", to: null }}
        otherConditions={[]}
        onApply={onApply}
      />,
    )

    const trigger = screen.getByRole("button", {
      name: "Filter Invoice Date",
    })
    expect(trigger).toHaveAttribute("aria-pressed", "true")
    await user.click(trigger)
    const from = screen.getByLabelText("From")
    expect(from).toHaveValue("2026-07-04")
    await user.clear(from)
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledWith(null)
  })
})

describe("a numeric column's funnel", () => {
  it("applies Min and Max as an open-ended range", async () => {
    const user = userEvent.setup()
    const onApply = vi.fn()
    render(
      <ColumnValueFilter
        api={NEVER_FETCH}
        view={VIEW}
        column={column("total", "Total", "decimal")}
        active={null}
        otherConditions={[]}
        onApply={onApply}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Filter Total" }))
    await user.type(screen.getByLabelText("Min"), "500")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledWith({
      kind: "range",
      from: "500",
      to: null,
    })
  })
})

describe("a text column's funnel", () => {
  it("switches to Contains and applies the trimmed fragment", async () => {
    const user = userEvent.setup()
    const onApply = vi.fn()
    render(
      <ColumnValueFilter
        api={NEVER_FETCH}
        view={VIEW}
        column={column("number", "Number", "string")}
        active={null}
        otherConditions={[]}
        onApply={onApply}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Filter Number" }))
    await user.click(screen.getByRole("button", { name: "Contains" }))
    await user.type(screen.getByLabelText("Contains"), "  INV-2026  ")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledWith({
      kind: "contains",
      text: "INV-2026",
    })
  })

  it("reopens in Contains mode when a contains filter is active", async () => {
    const user = userEvent.setup()
    render(
      <ColumnValueFilter
        api={NEVER_FETCH}
        view={VIEW}
        column={column("number", "Number", "string")}
        active={{ kind: "contains", text: "INV" }}
        otherConditions={[]}
        onApply={vi.fn()}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Filter Number" }))

    expect(screen.getByLabelText("Contains")).toHaveValue("INV")
  })
})
