import { cn } from "@/lib/utils"

/**
 * The tide line: a three-crest wave rule, TIDE's one signature mark.
 *
 * It appears in exactly three places -- under the connect headline, beneath
 * the sidebar wordmark, and beside a workspace title -- and nowhere else, so
 * it stays a mark rather than a texture. It draws in `currentColor` and
 * carries no motion; the quality bar for a business shell is met by being
 * quiet, not by moving.
 */
export function TideLine({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 76 10"
      width="76"
      height="10"
      fill="none"
      className={cn("shrink-0", className)}
    >
      <path
        d="M2 7 Q 8 1 14 7 T 26 7 T 38 7 T 50 7 T 62 7 T 74 7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  )
}
