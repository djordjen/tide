import { describe, expect, it } from "vitest"

import type {
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormField,
} from "@/lib/contracts"
import {
  acceptsNumericDraft,
  changedMutationPayload,
  collectionDraftRows,
  collectionEditorForm,
  formDraft,
  isEditableForm,
  mutationPayload,
  newCollectionDraft,
  normalizeNumericDraft,
  referenceSelectionDraft,
  shiftIsoDate,
  validateFormDraft,
} from "@/lib/form-draft"

describe("metadata-driven form drafts", () => {
  it("applies defaults and preserves exact decimal payload text", () => {
    const draft = formDraft(productForm)
    expect(draft).toEqual({
      code: "",
      unit_price: "",
      name: "",
      active: true,
    })

    const values = {
      ...draft,
      code: "SUP-NEW",
      unit_price: "1234.5",
      name: "Priority support",
    }
    expect(
      mutationPayload(
        productForm,
        values,
        new Set(["code", "unit_price", "name", "active"]),
      ),
    ).toEqual({
      code: "SUP-NEW",
      unit_price: "1234.5",
      name: "Priority support",
      active: true,
    })
  })

  it("validates masks, required fields, and decimal constraints", () => {
    const errors = validateFormDraft(
      productForm,
      {
        code: "lowercase",
        unit_price: "-1.000",
        name: "",
        active: true,
      },
      new Set(["code", "unit_price", "name", "active"]),
    )

    expect(errors).toEqual({
      code: "Code has an invalid format.",
      unit_price: "Unit Price has an invalid numeric format.",
      name: "Name is required.",
    })
  })

  it("enforces numeric typing and emits update changes only", () => {
    const price = productForm.fields.unit_price
    expect(acceptsNumericDraft(price, "10.2")).toBe(true)
    expect(acceptsNumericDraft(price, "10.234")).toBe(false)
    expect(normalizeNumericDraft(price, "10,2")).toBe("10.20")
    expect(shiftIsoDate("2026-07-11", 1)).toBe("2026-07-12")
    expect(shiftIsoDate("2026-07-11", -1)).toBe("2026-07-10")

    expect(
      changedMutationPayload(
        productForm,
        {
          code: "SUP",
          unit_price: "10.00",
          name: "Updated support",
          active: true,
        },
        new Set(["code", "unit_price", "name", "active"]),
        {
          id: 1,
          code: "SUP",
          unit_price: "10.00",
          name: "Support",
          active: true,
        },
      ),
    ).toEqual({ name: "Updated support" })
  })

  it("sends a selection draft in the server's wire types, not the input's", () => {
    // Every editor hands back a string. The server's selection decoder takes
    // an integer only as a JSON number, so a draft passed through unconverted
    // is refused whole and the selection assigns nothing.
    const payload = referenceSelectionDraft(invoiceLineForm, {
      line_number: "1",
      quantity: "3",
      description: "",
      product: null,
      taxable: false,
    })

    expect(payload).toEqual({
      line_number: 1,
      quantity: "3",
      taxable: false,
    })
    expect(typeof payload.line_number).toBe("number")
    expect(typeof payload.quantity).toBe("string")
  })

  it("leaves a selection draft's unfilled fields out rather than nulling them", () => {
    // A save replaces the record, so it states every field. A selection only
    // reports work in progress: a field nobody has reached is not a field
    // somebody cleared, and the server echoes back what it was sent.
    expect(
      referenceSelectionDraft(invoiceLineForm, formDraft(invoiceLineForm)),
    ).toEqual({ taxable: false })
  })
})

describe("editable form types", () => {
  it("does not lock a whole record out of editing over a uuid key", () => {
    // isEditableForm is all-or-nothing: one unrecognised type takes the edit
    // action away from every field on the record, not just its own.
    const guidForm: TideFormPresentation = {
      ...productForm,
      fields: {
        id: field({ name: "id", label: "Id", field_type: "uuid" }),
        name: field({ name: "name", label: "Name" }),
      },
    }

    expect(isEditableForm(guidForm)).toBe(true)
  })
})

const productForm: TideFormPresentation = {
  view: "catalog.Product.edit",
  entity: "catalog.Product",
  label: "Product",
  display_template: "{code} - {name}",
  fields: {
    code: field({
      name: "code",
      label: "Code",
      required: true,
      max_length: 30,
      regex: "[A-Z][A-Z0-9-]{0,29}",
    }),
    unit_price: field({
      name: "unit_price",
      label: "Unit Price",
      field_type: "decimal",
      required: true,
      numeric_mask: "0.00",
      precision: 12,
      scale: 2,
      minimum: "0",
    }),
    name: field({
      name: "name",
      label: "Name",
      required: true,
      max_length: 120,
    }),
    active: field({
      name: "active",
      label: "Active",
      field_type: "boolean",
      has_default: true,
      default_value: true,
    }),
  },
  sections: [
    {
      kind: "group",
      label: "Product",
      rows: [
        ["code", "unit_price"],
        ["name", "active"],
      ],
      tab: null,
    },
  ],
}

describe("collections the renderer knows nothing about", () => {
  it("numbers a new row through the field the manifest orders by", () => {
    const draft = newCollectionDraft(shipmentStops, [
      { position: "1" },
      { position: "2" },
    ])

    expect(draft.position).toBe("3")
  })

  it("leaves numbering alone when the manifest names no ordering field", () => {
    const draft = newCollectionDraft(
      { ...shipmentStops, sequence_field: null },
      [{ position: "1" }],
    )

    expect(draft.position).toBe("")
  })

  it("labels one row with the label the manifest sends", () => {
    const form = collectionEditorForm(shipmentStops)

    expect(form?.label).toBe("Entry")
  })

  it("keeps a record label no English pluralisation rule would produce", () => {
    const form = collectionEditorForm({
      ...shipmentStops,
      label: "Positionen",
      record_label: "Position",
    })

    expect(form?.label).toBe("Position")
  })
})

const invoiceLineForm: TideFormPresentation = {
  view: "sales.InvoiceLine.edit",
  entity: "sales.InvoiceLine",
  label: "Invoice Line",
  display_template: null,
  fields: {
    line_number: field({
      name: "line_number",
      label: "Line Number",
      field_type: "integer",
      required: true,
      numeric_mask: "0",
    }),
    quantity: field({
      name: "quantity",
      label: "Quantity",
      field_type: "decimal",
      required: true,
      numeric_mask: "0.000",
      precision: 12,
      scale: 3,
    }),
    description: field({
      name: "description",
      label: "Description",
      required: true,
      max_length: 200,
    }),
    product: field({
      name: "product",
      label: "Product",
      field_type: "reference",
      required: true,
      target_entity: "catalog.Product",
    }),
    taxable: field({
      name: "taxable",
      label: "Taxable",
      field_type: "boolean",
      has_default: true,
      default_value: false,
    }),
  },
  sections: [
    {
      kind: "group",
      label: "Line",
      rows: [
        ["line_number", "quantity"],
        ["description", "product"],
        ["taxable"],
      ],
      tab: null,
    },
  ],
}

const shipmentStops: TidePresentationFormCollection = {
  kind: "collection",
  name: "stops",
  label: "Entries",
  record_label: "Entry",
  entity: "logistics.ShipmentStop",
  view: "logistics.ShipmentStop.inline",
  identity_field: "id",
  sequence_field: "position",
  columns: [],
  fields: {
    position: field({
      name: "position",
      label: "Position",
      field_type: "integer",
    }),
  },
  groups: [
    { kind: "group", label: "Stop", rows: [["position"]], tab: null },
  ],
  tab: null,
}

function field(
  overrides: Partial<TidePresentationFormField> & {
    name: string
    label: string
  },
): TidePresentationFormField {
  const { name, label, ...fieldOverrides } = overrides
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
    ...fieldOverrides,
  }
}

describe("a collection draft row", () => {
  it("leaves the server's resolved reference names behind", () => {
    const rows = collectionDraftRows(lineCollection, [
      {
        id: 1,
        product: 4,
        description: "Consulting",
        _tide: {
          protected_fields: ["description"],
          references: { product: "CONS - Consulting hour" },
        },
      },
    ])

    // The row is about to be edited. `protected_fields` is a fact about the
    // field and survives; a resolved name belongs to the value it named, and
    // the point of a draft is that the value can change.
    expect(rows[0]._tide).toEqual({ protected_fields: ["description"] })
  })

  it("keeps an envelope that has nothing value-shaped in it", () => {
    const rows = collectionDraftRows(lineCollection, [
      { id: 1, product: 4, description: "Consulting", _tide: { protected_fields: ["description"] } },
    ])

    expect(rows[0]._tide).toEqual({ protected_fields: ["description"] })
  })
})

const productField: TidePresentationFormField = {
  name: "product",
  label: "Product",
  field_type: "reference",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: "catalog.Product",
  reference: null,
  values: [],
  writable: true,
  required: true,
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

const descriptionField: TidePresentationFormField = {
  name: "description",
  label: "Description",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
  values: [],
  writable: true,
  required: true,
  help: null,
  max_length: 200,
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

const lineCollection: TidePresentationFormCollection = {
  kind: "collection",
  name: "lines",
  label: "Lines",
  record_label: "Line",
  entity: "sales.InvoiceLine",
  view: "sales.InvoiceLine.inline",
  identity_field: "id",
  sequence_field: null,
  columns: [],
  fields: { product: productField, description: descriptionField },
  groups: [
    { kind: "group", label: "Line", rows: [["product", "description"]], tab: null },
  ],
  actions: [],
  draft_operations: ["create", "update"],
  writable: true,
  tab: null,
}
