import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type {
  TidePresentationColumn,
  TidePresentationFormField,
} from "@/lib/contracts"
import { acceptsNumericDraft, normalizeNumericDraft } from "@/lib/form-draft"
import { cn } from "@/lib/utils"

/**
 * One compact editor inside a grid row.
 *
 * The row is not a second form: the draft it edits was built by
 * `formDraft`, the save goes through `changedMutationPayload`, and this
 * component only decides how a 43px cell wears a control. The column
 * names the cell (there is no room for a label) and the form's field
 * metadata, when the detail form declares one, carries the masks and
 * bounds the form's own editor would enforce.
 */

interface GridCellEditorProps {
  column: TidePresentationColumn
  field?: TidePresentationFormField
  value: unknown
  error?: string
  disabled: boolean
  onChange: (value: unknown) => void
}

export function GridCellEditor({
  column,
  field,
  value,
  error,
  disabled,
  onChange,
}: GridCellEditorProps) {
  if (column.field_type === "boolean") {
    return (
      <Checkbox
        data-tide-editor
        aria-label={column.label}
        aria-invalid={Boolean(error)}
        title={error}
        className="cursor-pointer"
        checked={Boolean(value)}
        disabled={disabled}
        onCheckedChange={(checked) => onChange(checked === true)}
      />
    )
  }

  const captioned = field?.values?.length
    ? field.values
    : column.values?.length
      ? column.values
      : null
  if (captioned) {
    // A captioned field keeps its stored type: the picked option assigns
    // the declared code, not the string a listbox holds -- the same
    // contract the form's picker keeps.
    return (
      <CellSelect
        label={column.label}
        error={error}
        disabled={disabled}
        selected={value === null || value === undefined ? "" : String(value)}
        options={captioned.map((item) => ({
          key: String(item.value),
          label: item.label,
          pick: () => onChange(item.value),
        }))}
      />
    )
  }
  if (field?.choices?.length) {
    return (
      <CellSelect
        label={column.label}
        error={error}
        disabled={disabled}
        selected={value === null || value === undefined ? "" : String(value)}
        options={field.choices.map((choice) => ({
          key: choice,
          label: choice,
          pick: () => onChange(choice),
        }))}
      />
    )
  }

  const numeric =
    column.field_type === "integer" || column.field_type === "decimal"
  const text = value === null || value === undefined ? "" : String(value)
  return (
    <Input
      data-tide-editor
      aria-label={column.label}
      aria-invalid={Boolean(error)}
      title={error}
      type={
        column.field_type === "date"
          ? "date"
          : column.field_type === "datetime"
            ? "datetime-local"
            : "text"
      }
      inputMode={numeric ? "decimal" : undefined}
      maxLength={field?.max_length ?? undefined}
      className={cn(
        "h-8 bg-background px-2 text-sm",
        column.alignment === "right" && "text-right tabular-nums",
        error && "border-destructive/65",
      )}
      value={text}
      disabled={disabled}
      onChange={(event) => {
        const next = event.target.value
        if (numeric && field && !acceptsNumericDraft(field, next)) {
          return
        }
        onChange(next)
      }}
      onBlur={(event) => {
        if (numeric && field && event.target.value) {
          onChange(normalizeNumericDraft(field, event.target.value))
        }
      }}
    />
  )
}

function CellSelect({
  label,
  error,
  disabled,
  selected,
  options,
}: {
  label: string
  error?: string
  disabled: boolean
  selected: string
  options: readonly { key: string; label: string; pick: () => void }[]
}) {
  return (
    <Select
      value={selected === "" ? undefined : selected}
      disabled={disabled}
      onValueChange={(picked) => {
        options.find((option) => option.key === picked)?.pick()
      }}
    >
      <SelectTrigger
        data-tide-editor
        aria-label={label}
        aria-invalid={Boolean(error)}
        title={error}
        className={cn("h-8 px-2 text-sm", error && "border-destructive/65")}
      >
        <SelectValue placeholder="Select…" />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.key} value={option.key}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
