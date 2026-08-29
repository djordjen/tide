import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AttachmentField } from "@/components/attachment-field"
import type { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TidePresentationFormField,
} from "@/lib/contracts"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const ATTACHMENT = {
  identity: "ab3f9c72-5b84-4a11-9d0e-6c2f8a7b4e35",
  filename: "confirmation.pdf",
  size: 2048,
  content_type: "application/pdf",
}

function field(
  overrides: Partial<TidePresentationFormField> = {},
): TidePresentationFormField {
  return {
    name: "signed_document",
    label: "Signed document",
    field_type: "file",
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
    accept: ["pdf"],
    max_size_bytes: 10485760,
    upload_path: "/api/v1/invoices/_files/signed_document",
    ...overrides,
  } as TidePresentationFormField
}

const VIEW = {
  resource_path: "/api/v1/invoices",
  identity_field: "id",
  entity: "sales.Invoice",
} as TideBrowsePresentation

describe("the file field", () => {
  it("offers a picker when the record has no document yet", () => {
    render(
      <AttachmentField
        api={{} as TideApi}
        field={field()}
        value={null}
        writable
      />,
    )

    expect(
      screen.getByRole("button", { name: "Choose file" }),
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Download" })).toBeNull()
  })

  it("hands the draft the key the upload answered with", async () => {
    const uploadAttachment = vi.fn().mockResolvedValue(ATTACHMENT)
    const onChange = vi.fn()
    render(
      <AttachmentField
        api={{ uploadAttachment } as unknown as TideApi}
        field={field()}
        value={null}
        writable
        id="scan"
        onChange={onChange}
      />,
    )

    const picker = document.querySelector<HTMLInputElement>("#scan")
    await userEvent.upload(
      picker as HTMLInputElement,
      new File(["%PDF-1.4"], "confirmation.pdf", { type: "application/pdf" }),
    )

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(ATTACHMENT))
    expect(uploadAttachment).toHaveBeenCalledWith(
      "/api/v1/invoices/_files/signed_document",
      expect.any(File),
    )
  })

  it("names the document it holds and offers the three things one can do", () => {
    render(
      <AttachmentField
        api={{} as TideApi}
        field={field()}
        value={ATTACHMENT}
        view={VIEW}
        identity={7}
        writable
      />,
    )

    expect(screen.getByText("confirmation.pdf")).toBeInTheDocument()
    expect(screen.getByText("2.0 KB")).toBeInTheDocument()
    for (const name of ["Download", "Replace", "Delete"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument()
    }
  })

  it("will not offer to remove a document the model requires", () => {
    render(
      <AttachmentField
        api={{} as TideApi}
        field={field({ required: true })}
        value={ATTACHMENT}
        view={VIEW}
        identity={7}
        writable
      />,
    )

    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("leaves a locked document readable and nothing else", () => {
    render(
      <AttachmentField
        api={{} as TideApi}
        field={field()}
        value={ATTACHMENT}
        view={VIEW}
        identity={7}
        writable={false}
      />,
    )

    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Replace" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("empties the field when the document is deleted", async () => {
    const onChange = vi.fn()
    render(
      <AttachmentField
        api={{} as TideApi}
        field={field()}
        value={ATTACHMENT}
        view={VIEW}
        identity={7}
        writable
        onChange={onChange}
      />,
    )

    await userEvent.click(screen.getByRole("button", { name: "Delete" }))

    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("says so when an upload is refused, in the server's words", async () => {
    // The kind is refused by the server rather than by the picker on
    // purpose: `accept` is a convenience the browser applies, and what a
    // field will actually take is the server's answer either way.
    const { TideApiError } = await import("@/lib/api")
    const uploadAttachment = vi
      .fn()
      .mockRejectedValue(new TideApiError("that file is larger than 10mb"))
    render(
      <AttachmentField
        api={{ uploadAttachment } as unknown as TideApi}
        field={field()}
        value={null}
        writable
        id="scan"
      />,
    )

    await userEvent.upload(
      document.querySelector<HTMLInputElement>("#scan") as HTMLInputElement,
      new File(["%PDF-1.4"], "enormous.pdf", { type: "application/pdf" }),
    )

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "larger than 10mb",
    )
  })
})
