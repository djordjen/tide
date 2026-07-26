import { describe, expect, it } from "vitest"

import type { TidePresentationColumn } from "@/lib/contracts"
import {
  formatCellValue,
  formatRecordDisplay,
  formatReferenceDisplay,
} from "@/lib/format"

const money: TidePresentationColumn = {
  name: "total",
  label: "Total",
  field_type: "decimal",
  alignment: "right",
  format: "money",
  format_options: {
    decimal_places: 2,
    thousands_separator: true,
    display: null,
  },
  target_entity: null,
  reference: null,
}

it("formats exact decimal source text without binary float conversion", () => {
  expect(formatCellValue(money, "1000000000000000.50")).toBe(
    "1,000,000,000,000,000.50",
  )
})

it("uses the server-projected local date display pattern", () => {
  const date: TidePresentationColumn = {
    ...money,
    name: "invoice_date",
    label: "Invoice Date",
    field_type: "date",
    alignment: "left",
    format: "local_date",
    format_options: {
      decimal_places: null,
      thousands_separator: false,
      display: "%d.%m.%Y",
    },
  }
  expect(formatCellValue(date, "2026-07-15")).toBe("15.07.2026")
})

it("renders only fields named by the authorized reference template", () => {
  expect(
    formatReferenceDisplay(
      {
        entity: "crm.Customer",
        resource_path: "/api/v1/customers",
        identity_field: "id",
        display_template: "{code} - {name}",
      },
      { id: 7, code: "MORA", name: "Mora Trade" },
    ),
  ).toBe("MORA - Mora Trade")
})

it("renders field-name and template record displays safely", () => {
  const record = { id: 7, number: "INV-0007", code: "MORA", name: "Mora" }
  expect(formatRecordDisplay("number", record, "id")).toBe("INV-0007")
  expect(formatRecordDisplay("{code} - {name}", record, "id")).toBe(
    "MORA - Mora",
  )
  expect(formatRecordDisplay(null, record, "id")).toBe("7")
})

describe("protected fields", () => {
  it("never renders the supplied raw value", () => {
    expect(formatCellValue(money, "999.99", ["total"])).toBe("Protected")
  })
})
