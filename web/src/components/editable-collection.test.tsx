import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { EditableCollection } from "@/components/editable-collection"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { TideApi } from "@/lib/api"
import type {
  TidePresentationFormCollection,
  TidePresentationFormField,
} from "@/lib/contracts"

afterEach(cleanup)

describe("a collection widget that knows no application", () => {
  it("names the rows the manifest labels, not invoice lines", () => {
    renderCollection([])

    expect(screen.getByText("Stops")).toBeDefined()
    expect(screen.getByText("No Stops")).toBeDefined()
    expect(document.body.textContent).not.toContain("invoice")
    expect(document.body.textContent).not.toContain("line item")
  })

  it("labels each row control with the record the manifest names", () => {
    renderCollection([{ id: 1, position: "1" }])

    expect(screen.getByRole("button", { name: "Add Stop" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Apply Stop" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Remove Stop" })).toBeDefined()
  })
})

function renderCollection(rows: Array<Record<string, unknown>>) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <EditableCollection
          api={{} as TideApi}
          section={stops}
          forms={{}}
          rows={rows}
          errors={rows.map(() => ({}))}
          editable
          disabled={false}
          onRowsChange={() => {}}
          onErrorsChange={() => {}}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

const positionField: TidePresentationFormField = {
  name: "position",
  label: "Position",
  field_type: "integer",
  alignment: "right",
  values: [],
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
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

const stops: TidePresentationFormCollection = {
  kind: "collection",
  name: "stops",
  label: "Stops",
  record_label: "Stop",
  entity: "logistics.ShipmentStop",
  view: "logistics.ShipmentStop.inline",
  identity_field: "id",
  sequence_field: "position",
  columns: [
    {
      name: "position",
      label: "Position",
      alignment: "right",
      field_type: "integer",
      values: [],
      format: null,
      format_options: null,
      target_entity: null,
      reference: null,
    },
  ],
  fields: { position: positionField },
  groups: [
    { kind: "group", label: "Stop", rows: [["position"]], tab: null },
  ],
  actions: ["add", "apply", "remove"],
  draft_operations: ["create", "update"],
  writable: true,
  tab: null,
}
