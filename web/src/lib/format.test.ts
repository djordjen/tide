import { describe, expect, it } from "vitest"

import type {
  TidePresentationColumn,
  TidePresentationReference,
} from "@/lib/contracts"
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

describe("a display template with no placeholder", () => {
  /**
   * `display: uniqueid` is a field name, not a caption.
   *
   * Both formatters resolve the same declaration, and only one of them knew
   * that: a reference to an entity whose display is a bare field name showed
   * the literal word `uniqueid` in every picker, while the same declaration
   * read through `formatRecordDisplay` showed the value. The reference
   * applications all use `{braces}` or resolve through the embedded envelope,
   * which is why nothing here saw it.
   */
  const reference = {
    entity: "legacy.Equipment",
    display_template: "uniqueid",
  } as TidePresentationReference

  it("names the field to show, in both formatters", () => {
    const record = { uniqueid: "7F168", oid: 27 }

    expect(formatReferenceDisplay(reference, record)).toBe("7F168")
    expect(formatRecordDisplay("uniqueid", record, "oid")).toBe("7F168")
  })

  it("still fills in a template that has placeholders", () => {
    const braced = {
      ...reference,
      display_template: "{uniqueid} - {model}",
    } as TidePresentationReference

    expect(
      formatReferenceDisplay(braced, { uniqueid: "7F168", model: "NTP-SRV" }),
    ).toBe("7F168 - NTP-SRV")
  })
})
