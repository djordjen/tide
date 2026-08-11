import {
  Button,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@tide-framework/web"

/**
 * The parts render only inside an open menu, so every story composes the whole
 * thing. `defaultOpen` is what makes the content visible in a static card —
 * the trigger alone would screenshot as a lone button.
 */
export function ColumnMenu() {
  return (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">Columns</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Visible columns</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem checked>Number</DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem checked>Customer</DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem>Posted at</DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem>
          Reset
          <DropdownMenuShortcut>⌘R</DropdownMenuShortcut>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function RecordActions() {
  return (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">Actions</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuGroup>
          <DropdownMenuItem>Open</DropdownMenuItem>
          <DropdownMenuItem>Preview invoice</DropdownMenuItem>
          <DropdownMenuItem>Export CSV</DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled>Post invoice</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function WidthChoice() {
  return (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">Fit</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Column width</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value="best">
          <DropdownMenuRadioItem value="best">Best fit</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="fill">Fill</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="reset">Reset</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
