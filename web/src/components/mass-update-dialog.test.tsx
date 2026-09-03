// One field, one value, per-row answers: the dialog sends exactly the
// wire the door expects and reports what each row said, with warning-only
// refusals earning an amber second chance that resubmits only those rows.
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

import { MassUpdateDialog } from "@/components/mass-update-dialog"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideFormPresentation,
  TideMassUpdateResult,
  TidePresentationFormField,
  TideRecord,
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

function formField(
  name: string,
  label: string,
  fieldType: string,
  overrides: Partial<TidePresentationFormField> = {},
): TidePresentationFormField {
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
    writable: true,
    required: false,
    help: null,
    max_length: null,
    choices: [],
    regex: null,
    numeric_mask: null,
    precision: null,
    ...overrides,
  } as TidePresentationFormField
}

const view = {
  view: "demo.Job.browse",
  entity: "demo.Job",
  label: "Jobs",
  identity_field: "id",
  columns: [
    {
      name: "title",
      label: "Title",
      field_type: "string",
      alignment: "left",
      format: null,
      format_options: null,
      target_entity: null,
      reference: null,
      values: [],
    },
  ],
} as unknown as TideBrowsePresentation

const massUpdate = {
  path: "/api/v1/jobs/_mass-update",
  version_field: "version",
  limit: 1000,
}

const form = {
  view: "demo.Job.edit",
  entity: "demo.Job",
  label: "Job",
  fields: {
    title: formField("title", "Title", "string", { required: true }),
    priority: formField("priority", "Priority", "string"),
    worker: formField("worker", "Worker", "reference"),
    version: formField("version", "Version", "integer", {
      writable: false,
    }),
  },
} as unknown as TideFormPresentation

const records: TideRecord[] = [
  { id: 1, title: "Alpha", version: 3 },
  { id: 2, title: "Beta", version: null },
  { id: 3, title: "Gamma", version: 7 },
]

function renderDialog(
  massUpdateApi: (
    ...parameters: unknown[]
  ) => Promise<TideMassUpdateResult>,
  selected: ReadonlySet<string> = new Set(["1", "2"]),
) {
  const onApplied = vi.fn()
  render(
    <MassUpdateDialog
      api={{ massUpdate: massUpdateApi } as unknown as TideApi}
      view={view}
      massUpdate={massUpdate}
      form={form}
      records={records}
      selected={selected}
      onClose={() => {}}
      onApplied={onApplied}
    />,
  )
  return onApplied
}

async function pickField(label: string) {
  const user = userEvent.setup()
  await user.click(screen.getByRole("combobox", { name: "Field to change" }))
  await user.click(await screen.findByRole("option", { name: label }))
  return user
}

describe("the mass-update dialog", () => {
  it("offers writable scalars and never references", async () => {
    renderDialog(vi.fn())
    const user = userEvent.setup()
    await user.click(
      screen.getByRole("combobox", { name: "Field to change" }),
    )
    const options = await screen.findAllByRole("option")
    expect(options.map((option) => option.textContent)).toEqual([
      "Title",
      "Priority",
    ])
  })

  it("sends the change once with each row's observed version", async () => {
    const massUpdateApi = vi.fn().mockResolvedValue({
      outcomes: [
        {
          identity: 1,
          status: "updated",
          code: null,
          message: null,
          issues: [],
          notices: [],
          version: 4,
        },
        {
          identity: 2,
          status: "refused",
          code: "immutable_field",
          message: "field 'priority' cannot be changed: closed",
          issues: [],
          notices: [],
          version: null,
        },
      ],
      updated: 1,
      refused: 1,
    })
    const onApplied = renderDialog(massUpdateApi)

    const user = await pickField("Priority")
    await user.type(screen.getByRole("textbox", { name: "Priority" }), "high")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(massUpdateApi).toHaveBeenCalledWith(
      massUpdate,
      { priority: "high" },
      [
        { identity: 1, version: 3 },
        { identity: 2, version: "null" },
      ],
      [],
    )
    expect(
      await screen.findByText("Updated 1 of 2 records."),
    ).toBeInTheDocument()
    const refusals = screen.getAllByTestId("mass-update-refusal")
    expect(refusals).toHaveLength(1)
    expect(refusals[0].textContent).toContain("Beta")
    expect(refusals[0].textContent).toContain("cannot be changed")
    expect(onApplied).toHaveBeenCalledTimes(1)
  })

  it("resubmits only the warning-refused rows with the rules acknowledged", async () => {
    const massUpdateApi = vi
      .fn()
      .mockResolvedValueOnce({
        outcomes: [
          {
            identity: 1,
            status: "updated",
            code: null,
            message: null,
            issues: [],
            notices: [],
            version: 4,
          },
          {
            identity: 2,
            status: "refused",
            code: "validation_failed",
            message: "Hours are unusually high.",
            issues: [
              {
                rule: "heavy_hours",
                message: "Hours are unusually high.",
                fields: ["priority"],
                severity: "warning",
              },
            ],
            notices: [],
            version: null,
          },
        ],
        updated: 1,
        refused: 1,
      })
      .mockResolvedValueOnce({
        outcomes: [
          {
            identity: 2,
            status: "updated",
            code: null,
            message: null,
            issues: [],
            notices: [],
            version: 1,
          },
        ],
        updated: 1,
        refused: 0,
      })
    renderDialog(massUpdateApi)

    const user = await pickField("Priority")
    await user.type(screen.getByRole("textbox", { name: "Priority" }), "high")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(
      await screen.findByText("One row was refused only by warnings."),
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Apply anyway" }))

    expect(
      await screen.findByText("Updated 2 of 2 records."),
    ).toBeInTheDocument()
    expect(massUpdateApi).toHaveBeenLastCalledWith(
      massUpdate,
      { priority: "high" },
      [{ identity: 2, version: "null" }],
      ["heavy_hours"],
    )
    expect(screen.queryAllByTestId("mass-update-refusal")).toHaveLength(0)
  })

  it("refuses to apply an untouched value and blanks only by choice", async () => {
    const massUpdateApi = vi.fn().mockResolvedValue({
      outcomes: [],
      updated: 0,
      refused: 0,
    })
    renderDialog(massUpdateApi, new Set(["1"]))

    const user = await pickField("Priority")
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()

    await user.click(
      screen.getByRole("checkbox", { name: "Clear the field" }),
    )
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(massUpdateApi).toHaveBeenCalledWith(
      massUpdate,
      { priority: null },
      [{ identity: 1, version: 3 }],
      [],
    )
  })
})
