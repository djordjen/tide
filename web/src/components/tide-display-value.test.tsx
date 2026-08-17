import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TideDisplayValue } from "@/components/tide-display-value"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationColumn,
  TideRecord,
} from "@/lib/contracts"

afterEach(cleanup)

describe("a reference cell", () => {
  it("shows the name that arrived with the row, without asking who it is", async () => {
    const getReference = vi.fn(() => {
      throw new Error("a resolved reference must not be fetched again")
    })

    renderCell(
      {
        id: 7,
        customer: 4,
        _tide: { references: { customer: "ADRIA - Adria Consulting" } },
      },
      getReference,
    )

    expect(await screen.findByText("ADRIA - Adria Consulting")).toBeDefined()
    expect(getReference).not.toHaveBeenCalled()
  })

  it("asks when the row carried no name for it", async () => {
    const getReference = vi.fn(async () => ({ id: 4, code: "MORA", name: "Mora Trade" }))

    renderCell({ id: 7, customer: 4 }, getReference)

    // A server that resolves nothing -- an older one, or one refusing this
    // reference -- leaves the client doing exactly what it always did.
    expect(await screen.findByText("MORA - Mora Trade")).toBeDefined()
    expect(getReference).toHaveBeenCalledTimes(1)
  })

  it("does not flash the raw key while the name is on its way", () => {
    const getReference = vi.fn(() => new Promise(() => {}))

    renderCell({ id: 7, customer: 4 }, getReference)

    // The key is the database's spelling of the reference, not its name; a
    // cell that shows it for a frame teaches people to read keys.
    expect(screen.queryByText("4")).toBeNull()
    expect(screen.getByText("…")).toBeDefined()
  })

  it("will not name a field it was told to withhold", async () => {
    const getReference = vi.fn(() => {
      throw new Error("a protected reference must not be fetched")
    })

    renderCell(
      {
        id: 7,
        customer: null,
        _tide: {
          protected_fields: ["customer"],
          references: { customer: "ADRIA - Adria Consulting" },
        },
      },
      getReference,
    )

    // A name is a value. The server does not send both today, and the cell
    // must not be the thing that assumes it never will.
    expect(await screen.findByText("Protected")).toBeDefined()
    expect(screen.queryByText("ADRIA - Adria Consulting")).toBeNull()
    expect(getReference).not.toHaveBeenCalled()
  })
})

describe("a choice chip", () => {
  it("wears the same identity tint for the same value on every screen", () => {
    // The tint says "these rows are the same state", not "this state is
    // good": the framework cannot know what Posted means to an application,
    // so color is identity, and identity must be stable.
    const first = renderChoice("Posted")
    const tint = first
      .getByText("Posted")
      .closest("[data-tint]")
      ?.getAttribute("data-tint")
    expect(tint).toBeTruthy()
    first.unmount()

    const again = renderChoice("Posted")
    expect(
      again
        .getByText("Posted")
        .closest("[data-tint]")
        ?.getAttribute("data-tint"),
    ).toBe(tint)
  })
})

function renderChoice(value: string) {
  const column: TidePresentationColumn = {
    name: "status",
    label: "Status",
    field_type: "choice",
    alignment: "left",
    values: [],
    format: null,
    format_options: null,
    target_entity: null,
    reference: null,
  }
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <TideDisplayValue
        api={{} as unknown as TideApi}
        column={column}
        record={{ id: 1, status: value }}
      />
    </QueryClientProvider>,
  )
}

function renderCell(record: TideRecord, getReference: unknown) {
  const column: TidePresentationColumn = {
    name: "customer",
    label: "Customer",
    field_type: "reference",
    alignment: "left",
    values: [],
    format: null,
    format_options: null,
    target_entity: "crm.Customer",
    reference: {
      entity: "crm.Customer",
      resource_path: "/api/v1/customers",
      identity_field: "id",
      display_template: "{code} - {name}",
    },
  }
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <TideDisplayValue
        api={{ getReference } as unknown as TideApi}
        column={column}
        record={record}
      />
    </QueryClientProvider>,
  )
}
