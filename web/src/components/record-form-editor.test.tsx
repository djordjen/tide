// The reference well carries the affordances the reference application's
// editors taught: a door to the referenced record where the manifest offers
// a screen for it, and a clear control -- only where the model says empty is
// a legal value. Both live inside the well beside the picker.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RecordFormEditor } from "@/components/record-form-editor"
import type { TideApi } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationManifest,
} from "@/lib/contracts"

afterEach(cleanup)

describe("a reference control", () => {
  it("opens the referenced record where the manifest offers a screen", async () => {
    renderEditor({ views })

    const open = await screen.findByRole("link", { name: "Open Customer" })
    // A new tab, so an open draft can never be lost to a side trip.
    expect(open).toHaveAttribute("target", "_blank")
    expect(open.getAttribute("href")).toContain("view=crm.Customer.browse")
    expect(open.getAttribute("href")).toContain("record=4")
  })

  it("offers no door when the manifest has no screen for the target", () => {
    renderEditor({ views: {} })

    expect(
      screen.queryByRole("link", { name: "Open Customer" }),
    ).toBeNull()
  })

  it("clears an optional reference and leaves a required one alone", async () => {
    const onApplyValues = vi.fn()
    const user = userEvent.setup()
    renderEditor({ views, onApplyValues })

    // Customer is required: emptying it could only manufacture a refusal,
    // so the control does not offer to.
    expect(
      screen.queryByRole("button", { name: "Clear Customer" }),
    ).toBeNull()

    await user.click(screen.getByRole("button", { name: "Clear Agent" }))
    expect(onApplyValues).toHaveBeenCalledWith({ agent: null })
  })
})

function renderEditor(options: {
  views: TidePresentationManifest["views"]
  onApplyValues?: (values: Record<string, unknown>) => void
}) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RecordFormEditor
        api={referenceApi()}
        form={form}
        forms={{}}
        views={options.views}
        draft={{ customer: 4, agent: 7 }}
        editableFields={new Set(["customer", "agent"])}
        errors={{}}
        disabled={false}
        onChange={vi.fn()}
        onApplyValues={options.onApplyValues ?? vi.fn()}
      />
    </QueryClientProvider>,
  )
}

function referenceApi(): TideApi {
  return {
    getReference: async (
      _reference: unknown,
      value: unknown,
    ) => ({ id: value, code: `C${String(value)}`, name: "Someone" }),
  } as unknown as TideApi
}

const plainColumn = {
  name: "code",
  label: "Code",
  field_type: "string",
  alignment: "left",
  values: [],
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
}

const baseField = {
  ...plainColumn,
  writable: true,
  lookup: null,
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

function referenceLookup(field: string, entity: string) {
  return {
    view: `${entity}.lookup`,
    title: `Select ${field}`,
    owner_entity: "sales.Invoice",
    field,
    target_entity: entity,
    resource_path: "/api/v1/records",
    query_path: "/api/v1/records/_query",
    selection_path: "/api/v1/_tide/reference-selection",
    identity_field: "id",
    columns: [plainColumn],
    search_fields: ["code"],
    page_size: 20,
    operations: ["list", "get"],
    create_view: null,
  }
}

const form = {
  view: "sales.Invoice.edit",
  entity: "sales.Invoice",
  label: "Invoice",
  display_template: "number",
  fields: {
    customer: {
      ...baseField,
      name: "customer",
      label: "Customer",
      field_type: "reference",
      target_entity: "crm.Customer",
      reference: {
        entity: "crm.Customer",
        resource_path: "/api/v1/customers",
        identity_field: "id",
        display_template: "{code} - {name}",
      },
      lookup: referenceLookup("customer", "crm.Customer"),
      required: true,
    },
    agent: {
      ...baseField,
      name: "agent",
      label: "Agent",
      field_type: "reference",
      target_entity: "crm.Agent",
      reference: {
        entity: "crm.Agent",
        resource_path: "/api/v1/agents",
        identity_field: "id",
        display_template: "{code} - {name}",
      },
      lookup: referenceLookup("agent", "crm.Agent"),
      required: false,
    },
  },
  sections: [
    {
      kind: "group",
      label: "Header",
      rows: [["customer"], ["agent"]],
      tab: null,
    },
  ],
  actions: [],
} as unknown as TideFormPresentation

const views = {
  "crm.Customer.browse": {
    view: "crm.Customer.browse",
    entity: "crm.Customer",
    label: "Customers",
    resource_path: "/api/v1/customers",
    query_path: "/api/v1/customers/_query",
    identity_field: "id",
    columns: [plainColumn],
    search_field: null,
    search_label: null,
    named_filters: [],
    sortable_fields: [],
    page_size: 25,
    operations: ["list", "get"],
    detail_view: "crm.Customer.edit",
  },
} as unknown as TidePresentationManifest["views"]
