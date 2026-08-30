// How a declared parameter renders as an input, and the form an action
// with required input opens before it runs -- shared by the report
// parameter bar and the action dialog, so both surfaces collect the same
// string forms the services type.
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type {
  TideParameter,
  TideParameterType,
  TidePresentationFormAction,
} from "@/lib/contracts"

export function parameterInputType(type: TideParameterType): string {
  // Native date controls collect exactly the ISO strings the services
  // parse, and give the phone-sized Web surface a real picker.
  return type === "date"
    ? "date"
    : type === "datetime"
      ? "datetime-local"
      : "text"
}

export function parameterInputMode(
  type: TideParameterType,
): "numeric" | "decimal" | undefined {
  return type === "integer"
    ? "numeric"
    : type === "decimal"
      ? "decimal"
      : undefined
}

export function parameterPlaceholder(
  type: TideParameterType,
): string | undefined {
  return type === "integer"
    ? "a whole number"
    : type === "decimal"
      ? "a number"
      : type === "boolean"
        ? "true or false"
        : type === "string"
          ? "text"
          : undefined
}

// The dialog rule: a required parameter is a question a person must
// answer, so it opens the form; an optional-only action stays one click
// and its parameters remain a programmatic door.
export function actionOpensDialog(
  action: TidePresentationFormAction,
): boolean {
  return (action.parameters ?? []).some((parameter) => parameter.required)
}

function collectValues(
  parameters: readonly TideParameter[],
  draft: Record<string, string>,
): Record<string, string> {
  const supplied: Record<string, string> = {}
  for (const parameter of parameters) {
    const value = (draft[parameter.name] ?? "").trim()
    if (value) {
      supplied[parameter.name] = value
    }
  }
  return supplied
}

export function ActionParametersForm({
  action,
  onRun,
}: {
  action: TidePresentationFormAction
  onRun: (parameters: Record<string, string>) => void
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const declared = action.parameters ?? []
  const ready = declared.every(
    (parameter) =>
      !parameter.required || (draft[parameter.name] ?? "").trim(),
  )
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!ready) {
          return
        }
        onRun(collectValues(declared, draft))
      }}
    >
      {declared.map((parameter) => (
        <label
          key={parameter.name}
          className="flex flex-col gap-1 text-xs font-medium"
        >
          <span className="text-muted-foreground">
            {parameter.label}
            {parameter.required ? " (required)" : ""}
          </span>
          <Input
            className="h-8"
            aria-label={parameter.label}
            type={parameterInputType(parameter.type)}
            inputMode={parameterInputMode(parameter.type)}
            placeholder={parameterPlaceholder(parameter.type)}
            autoFocus={parameter === declared[0]}
            value={draft[parameter.name] ?? ""}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                [parameter.name]: event.target.value,
              }))
            }
          />
        </label>
      ))}
      <div className="flex justify-end">
        <Button size="sm" type="submit" disabled={!ready}>
          {action.label}
        </Button>
      </div>
    </form>
  )
}
