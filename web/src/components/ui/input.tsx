import * as React from "react"

import { cn } from "@/lib/utils"

const Input = React.forwardRef<
  HTMLInputElement,
  React.ComponentProps<"input">
>(({ className, type, ...props }, ref) => (
  <input
    type={type}
    className={cn(
      // A well, not a chip: text controls are filled a step below the card so
      // "type here" and "click here" stop wearing the same coat -- buttons
      // keep the raised card background.
      "flex h-9 w-full rounded-lg border border-input bg-muted/55 px-3 py-1 text-sm shadow-xs outline-none transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/25",
      className,
    )}
    ref={ref}
    {...props}
  />
))
Input.displayName = "Input"

export { Input }
