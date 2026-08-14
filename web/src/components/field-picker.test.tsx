/**
 * The closed-set editor, driven the way a person drives it.
 *
 * It used to be a native `<select>` and nothing here exercised it, so moving
 * it onto the code-owned control had no test to break and none to trust. The
 * two things worth pinning are the two that a listbox makes easy to get wrong:
 * a captioned code must come back as the code and not as the string an option
 * value always is, and the trigger has to keep the `data-tide-editor` mark the
 * Enter traversal and the density journey both look for.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

import { RecordFormEditor } from "@/components/record-form-editor"
import type { TideApi } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationFormField,
} from "@/lib/contracts"

// Radix listboxes measure and capture pointers, which jsdom does not
// implement. These are the standard shims, kept here rather than globally so
// it is obvious which component needs them.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.setPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never
})

afterEach(cleanup)

const status = {
  name: "status",
  label: "Status",
  field_type: "integer",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
  values: [
    { value: 0, label: "Ordered" },
    { value: 2, label: "In repair" },
  ],
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
  lookup: null,
  has_default: false,
  default_value: null,
} as unknown as TidePresentationFormField

const form = {
  view: "demo.Item.edit",
  entity: "demo.Item",
  label: "Item",
  identity_field: "id",
  fields: { status },
  sections: [{ kind: "group", label: "Item", rows: [["status"]] }],
} as unknown as TideFormPresentation

function renderEditor(onChange = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RecordFormEditor
        api={{} as TideApi}
        form={form}
        forms={{}}
        draft={{ status: 2 } as never}
        editableFields={new Set(["status"])}
        errors={{}}
        disabled={false}
        onChange={onChange}
        onApplyValues={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return onChange
}

describe("the closed-set field editor", () => {
  it("shows the caption of the stored code", () => {
    renderEditor()
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveTextContent(
      "In repair",
    )
  })

  it("hands back the declared code, not the option string", async () => {
    const onChange = renderEditor()
    await userEvent.click(screen.getByRole("combobox", { name: "Status" }))
    await userEvent.click(screen.getByRole("option", { name: "Ordered" }))

    // 0, not "0": the column is an integer and the map declared integers.
    expect(onChange).toHaveBeenCalledWith("status", 0)
  })

  it("keeps the mark the Enter traversal and the density check look for", () => {
    renderEditor()
    const trigger = screen.getByRole("combobox", { name: "Status" })
    expect(trigger).toHaveAttribute("data-tide-editor")
  })
})
