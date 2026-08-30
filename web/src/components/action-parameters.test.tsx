// The dialog an action with required input opens before it runs. The form
// owns no transport: what these tests pin is the dialog rule (required
// opens, optional-only stays one click), that the run button waits for
// every required value, and that submit hands over trimmed strings with
// blanks omitted -- the service does the typing.
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ActionParametersForm,
  actionOpensDialog,
} from "@/components/parameters"
import type { TidePresentationFormAction } from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const VOID_ACTION: TidePresentationFormAction = {
  name: "void",
  label: "Void",
  idempotent: true,
  parameters: [
    { name: "reason", label: "Reason", type: "string", required: true },
    { name: "note", label: "Note", type: "string", required: false },
  ],
}

const POST_ACTION: TidePresentationFormAction = {
  name: "post",
  label: "Post",
  idempotent: true,
  parameters: [
    {
      name: "occurred_at",
      label: "Occurred At",
      type: "datetime",
      required: false,
    },
  ],
}

describe("the dialog rule", () => {
  it("opens only for an action with a required parameter", () => {
    expect(actionOpensDialog(VOID_ACTION)).toBe(true)
    expect(actionOpensDialog(POST_ACTION)).toBe(false)
    expect(
      actionOpensDialog({
        name: "touch",
        label: "Touch",
        idempotent: false,
        parameters: [],
      }),
    ).toBe(false)
  })
})

describe("the parameters form", () => {
  it("offers every declared parameter and waits for the required ones", async () => {
    const user = userEvent.setup()
    render(<ActionParametersForm action={VOID_ACTION} onRun={vi.fn()} />)

    expect(screen.getByLabelText("Reason")).toBeInTheDocument()
    expect(screen.getByLabelText("Note")).toBeInTheDocument()
    const run = screen.getByRole("button", { name: "Void" })
    expect(run).toBeDisabled()

    await user.type(screen.getByLabelText("Reason"), "Ordered twice")
    expect(run).toBeEnabled()
  })

  it("submits trimmed values and omits the blanks", async () => {
    const user = userEvent.setup()
    const onRun = vi.fn()
    render(<ActionParametersForm action={VOID_ACTION} onRun={onRun} />)

    await user.type(screen.getByLabelText("Reason"), "  Ordered twice  ")
    await user.click(screen.getByRole("button", { name: "Void" }))

    expect(onRun).toHaveBeenCalledWith({ reason: "Ordered twice" })
  })

  it("submits from the keyboard with Enter", async () => {
    const user = userEvent.setup()
    const onRun = vi.fn()
    render(<ActionParametersForm action={VOID_ACTION} onRun={onRun} />)

    await user.type(screen.getByLabelText("Reason"), "Damaged{Enter}")

    expect(onRun).toHaveBeenCalledWith({ reason: "Damaged" })
  })
})
