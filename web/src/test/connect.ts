import { screen } from "@testing-library/react"
import type { UserEvent } from "@testing-library/user-event"

/** The token `connectWithToken` signs in with, for asserting on what it sent. */
export const TOKEN = "a-development-token-that-is-long-enough"

/** Sign in with the development token, the way every journey test starts.
 *
 * The token is pasted rather than typed. `user.type` yields to the event loop
 * after each character so the application can react to it, which is the right
 * default -- a keystroke really does re-render the shell, and tests that clear
 * a field and type into it depend on that render landing. But nothing here is
 * testing what happens between the letters of a fixture token, and paying for
 * thirty-nine renders of the whole application was around a third of the
 * runtime of the longest tests, which is what left them short of the 5s limit
 * whenever the other suites were competing for a core.
 */
export async function connectWithToken(user: UserEvent) {
  await user.click(await screen.findByLabelText("Application token"))
  await user.paste(TOKEN)
  await user.click(screen.getByRole("button", { name: "Connect securely" }))
}
