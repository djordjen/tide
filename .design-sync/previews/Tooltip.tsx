import {
  Button,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@tide-framework/web"

/**
 * `TooltipProvider` is required — without it the trigger throws. `defaultOpen`
 * is what puts the content in a static screenshot; the content portals, so the
 * card is pinned to a single story.
 */
export function FieldHint() {
  return (
    <TooltipProvider>
      <Tooltip defaultOpen>
        <TooltipTrigger asChild>
          <Button variant="outline">Post invoice</Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          A posted invoice can no longer be edited
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
