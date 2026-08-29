import { Download, Paperclip, RefreshCw, Trash2 } from "lucide-react"
import { useRef, useState, type ReactElement } from "react"

import { Button } from "@/components/ui/button"
import { TideApiError, type TideApi } from "@/lib/api"
import type {
  TideAttachmentValue,
  TideBrowsePresentation,
  TidePresentationFormField,
} from "@/lib/contracts"
import { cn } from "@/lib/utils"

interface AttachmentFieldProps {
  api: TideApi
  field: TidePresentationFormField
  value: unknown
  /** The browse this record was opened from: how a download is addressed. */
  view?: TideBrowsePresentation | null
  identity?: unknown
  writable: boolean
  disabled?: boolean
  error?: string
  id?: string
  describedBy?: string
  /**
   * Hands back the whole projection rather than the key alone: the draft
   * shows a name and a size for a file that has only just been chosen, and
   * the key is taken out again where the value goes to the server.
   */
  onChange?: (value: TideAttachmentValue | null) => void
}

/**
 * A document on a record: pick one, fetch it back, swap it, take it off.
 *
 * The draft holds the key the upload answered with, and the record claims
 * that key when it saves -- so choosing a file does not change the record
 * and cancelling the form leaves nothing behind but bytes the server
 * reclaims on its own.
 */
export function AttachmentField({
  api,
  field,
  value,
  view,
  identity,
  writable,
  disabled = false,
  error,
  id,
  describedBy,
  onChange,
}: AttachmentFieldProps): ReactElement {
  const picker = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<"upload" | "download" | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const attachment = asAttachment(value)
  const uploadPath = field.upload_path ?? null
  const accept = (field.accept ?? []).map((extension) => `.${extension}`).join(",")

  async function stage(file: File): Promise<void> {
    if (!uploadPath) {
      return
    }
    setBusy("upload")
    setFailure(null)
    try {
      const staged = await api.uploadAttachment(uploadPath, file)
      onChange?.(staged)
    } catch (cause) {
      setFailure(
        cause instanceof TideApiError
          ? cause.message
          : "The file could not be uploaded.",
      )
    } finally {
      setBusy(null)
      // Cleared so choosing the same file twice still counts as a change:
      // a refused upload the person retries unchanged is the common case.
      if (picker.current) {
        picker.current.value = ""
      }
    }
  }

  async function fetchFile(): Promise<void> {
    if (!attachment || !view || identity === undefined || identity === null) {
      return
    }
    setBusy("download")
    setFailure(null)
    try {
      const download = await api.downloadAttachment(view, identity, field.name)
      save(download.blob, download.filename)
    } catch (cause) {
      setFailure(
        cause instanceof TideApiError
          ? cause.message
          : "The file could not be downloaded.",
      )
    } finally {
      setBusy(null)
    }
  }

  const controls = (
    <input
      ref={picker}
      id={id}
      type="file"
      accept={accept || undefined}
      className="sr-only"
      aria-invalid={Boolean(error)}
      aria-describedby={describedBy}
      disabled={disabled || busy !== null}
      onChange={(event) => {
        const file = event.target.files?.[0]
        if (file) {
          void stage(file)
        }
      }}
    />
  )

  if (!attachment) {
    return (
      <div className="flex flex-col gap-1.5">
        {controls}
        {writable && uploadPath ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn("w-fit", error && "border-destructive/65")}
            disabled={disabled || busy !== null}
            onClick={() => picker.current?.click()}
          >
            <Paperclip aria-hidden="true" />
            {busy === "upload" ? "Uploading…" : "Choose file"}
          </Button>
        ) : (
          <span className="text-sm text-muted-foreground">No file</span>
        )}
        {failure ? (
          <span role="alert" className="text-xs text-destructive">
            {failure}
          </span>
        ) : null}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5">
      {controls}
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-sm" title={attachment.filename}>
          {attachment.filename}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {formatSize(attachment.size)}
        </span>
      </span>
      <span className="flex flex-wrap gap-1.5">
        {view && identity !== undefined && identity !== null ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void fetchFile()}
          >
            <Download aria-hidden="true" />
            {busy === "download" ? "Downloading…" : "Download"}
          </Button>
        ) : null}
        {writable && uploadPath ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || busy !== null}
            onClick={() => picker.current?.click()}
          >
            <RefreshCw aria-hidden="true" />
            {busy === "upload" ? "Uploading…" : "Replace"}
          </Button>
        ) : null}
        {/*
          A required field offers Replace and not Delete: a document the
          model insists on can be exchanged for a better copy, never
          removed, or this control would build a record its own validation
          refuses to save.
        */}
        {writable && uploadPath && !field.required ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || busy !== null}
            onClick={() => {
              setFailure(null)
              onChange?.(null)
            }}
          >
            <Trash2 aria-hidden="true" />
            Delete
          </Button>
        ) : null}
      </span>
      {failure ? (
        <span role="alert" className="text-xs text-destructive">
          {failure}
        </span>
      ) : null}
    </div>
  )
}

function asAttachment(value: unknown): TideAttachmentValue | null {
  if (!value || typeof value !== "object") {
    return null
  }
  const candidate = value as Partial<TideAttachmentValue>
  return typeof candidate.identity === "string" &&
    typeof candidate.filename === "string"
    ? (candidate as TideAttachmentValue)
    : null
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const kilobytes = bytes / 1024
  if (kilobytes < 1024) {
    return `${kilobytes.toFixed(kilobytes < 10 ? 1 : 0)} KB`
  }
  return `${(kilobytes / 1024).toFixed(kilobytes / 1024 < 10 ? 1 : 0)} MB`
}

function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.style.display = "none"
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
