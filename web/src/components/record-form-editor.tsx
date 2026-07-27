import type { CSSProperties, KeyboardEvent } from "react"

import { TideDisplayValue } from "@/components/tide-display-value"
import type { TideApi } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationFormField,
  TidePresentationFormGroup,
  TideRecord,
} from "@/lib/contracts"
import {
  acceptsNumericDraft,
  normalizeNumericDraft,
  shiftIsoDate,
  type TideFormDraft,
  type TideFormErrors,
} from "@/lib/form-draft"
import { cn } from "@/lib/utils"

interface RecordFormEditorProps {
  api: TideApi
  form: TideFormPresentation
  draft: TideFormDraft
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  onChange: (name: string, value: unknown) => void
}

export function RecordFormEditor({
  api,
  form,
  draft,
  editableFields,
  errors,
  disabled,
  onChange,
}: RecordFormEditorProps) {
  const record = draft as TideRecord
  return (
    <>
      {form.sections.map((section, index) =>
        section.kind === "group" ? (
          <EditorGroup
            key={`editor-${index}-${section.label}`}
            api={api}
            form={form}
            record={record}
            section={section}
            editableFields={editableFields}
            errors={errors}
            disabled={disabled}
            onChange={onChange}
          />
        ) : null,
      )}
    </>
  )
}

function EditorGroup({
  api,
  form,
  record,
  section,
  editableFields,
  errors,
  disabled,
  onChange,
}: {
  api: TideApi
  form: TideFormPresentation
  record: TideRecord
  section: TidePresentationFormGroup
  editableFields: ReadonlySet<string>
  errors: TideFormErrors
  disabled: boolean
  onChange: (name: string, value: unknown) => void
}) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">{section.label}</h2>
      <div className="space-y-3">
        {section.rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className="tide-form-row grid gap-3"
            style={
              {
                "--tide-form-columns": row.length,
              } as CSSProperties
            }
          >
            {row.map((name) => {
              const field = form.fields[name]
              const editable = editableFields.has(name)
              return (
                <div
                  key={name}
                  className={cn(
                    "min-w-0 rounded-xl border px-3.5 py-3",
                    editable
                      ? "bg-background"
                      : "border-border/75 bg-muted/35",
                    errors[name] &&
                      "border-destructive/60 ring-2 ring-destructive/10",
                  )}
                >
                  {editable ? (
                    <FieldEditor
                      field={field}
                      value={record[name]}
                      error={errors[name]}
                      disabled={disabled}
                      onChange={(value) => onChange(name, value)}
                    />
                  ) : (
                    <>
                      <p className="mb-1.5 truncate text-xs font-medium text-muted-foreground">
                        {field.label}
                      </p>
                      <TideDisplayValue
                        api={api}
                        column={field}
                        record={record}
                        wrap
                        className="min-h-5 text-sm"
                      />
                    </>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

function FieldEditor({
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  field: TidePresentationFormField
  value: unknown
  error?: string
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  const id = `tide-editor-${field.name}`
  const helpId = `${id}-help`
  const errorId = `${id}-error`
  const describedBy = error ? errorId : field.help ? helpId : undefined

  if (field.field_type === "boolean") {
    return (
      <div>
        <label
          htmlFor={id}
          className="flex min-h-9 cursor-pointer items-center gap-2.5 text-sm font-medium"
        >
          <input
            id={id}
            data-tide-editor
            type="checkbox"
            className="size-4 rounded border-border accent-primary"
            checked={Boolean(value)}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-describedby={describedBy}
            onChange={(event) => onChange(event.target.checked)}
            onKeyDown={moveOnEnter}
          />
          {field.label}
        </label>
        <FieldMessage
          field={field}
          error={error}
          helpId={helpId}
          errorId={errorId}
        />
      </div>
    )
  }

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block truncate text-xs font-medium text-muted-foreground"
      >
        {field.label}
        {field.required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {field.field_type === "choice" ? (
        <select
          id={id}
          data-tide-editor
          className={editorClass(error)}
          value={String(value ?? "")}
          disabled={disabled}
          required={field.required}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={moveOnEnter}
        >
          {!field.required ? <option value="">None</option> : null}
          {field.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice.replace(/[_-]+/g, " ")}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          data-tide-editor
          className={editorClass(error)}
          type={inputType(field)}
          inputMode={
            field.field_type === "decimal"
              ? "decimal"
              : field.field_type === "integer"
                ? "numeric"
                : undefined
          }
          value={String(value ?? "")}
          disabled={disabled}
          required={field.required}
          maxLength={field.max_length ?? undefined}
          pattern={field.regex ?? undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          onChange={(event) => {
            const next = event.target.value
            if (
              field.field_type !== "decimal" &&
              field.field_type !== "integer"
            ) {
              onChange(next)
            } else if (acceptsNumericDraft(field, next)) {
              onChange(next)
            }
          }}
          onBlur={(event) => {
            if (
              field.field_type === "decimal" ||
              field.field_type === "integer"
            ) {
              onChange(normalizeNumericDraft(field, event.target.value))
            }
          }}
          onKeyDown={(event) => {
            if (
              field.field_type === "date" &&
              (event.key === "+" || event.key === "-")
            ) {
              event.preventDefault()
              const current =
                String(value ?? "") ||
                new Date().toISOString().slice(0, 10)
              onChange(shiftIsoDate(current, event.key === "+" ? 1 : -1))
              return
            }
            moveOnEnter(event)
          }}
        />
      )}
      <FieldMessage
        field={field}
        error={error}
        helpId={helpId}
        errorId={errorId}
      />
    </div>
  )
}

function FieldMessage({
  field,
  error,
  helpId,
  errorId,
}: {
  field: TidePresentationFormField
  error?: string
  helpId: string
  errorId: string
}) {
  if (error) {
    return (
      <p id={errorId} className="mt-1.5 text-xs text-destructive">
        {error}
      </p>
    )
  }
  return field.help ? (
    <p id={helpId} className="mt-1.5 text-xs text-muted-foreground">
      {field.help}
    </p>
  ) : null
}

function editorClass(error?: string): string {
  return cn(
    "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none transition-shadow placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-60",
    error && "border-destructive/65 focus:border-destructive",
  )
}

function inputType(field: TidePresentationFormField): string {
  if (field.field_type === "date") {
    return "date"
  }
  if (field.field_type === "datetime") {
    return "datetime-local"
  }
  if (field.validations.includes("email")) {
    return "email"
  }
  return "text"
}

function moveOnEnter(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "Enter") {
    return
  }
  event.preventDefault()
  const editors = Array.from(
    document.querySelectorAll<HTMLElement>("[data-tide-editor]:not(:disabled)"),
  )
  const index = editors.indexOf(event.currentTarget)
  editors[index + 1]?.focus()
}
