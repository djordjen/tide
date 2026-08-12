import { lazy, Suspense, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { ConnectionScreen } from "@/components/connection-screen"
import { SHELL_TITLE, useDocumentTitle } from "@/lib/document-title"
import { TideApi, TideApiError } from "@/lib/api"
import type {
  TideBrowserAuthenticationInfo,
  TideConnection,
} from "@/lib/contracts"

/**
 * The shell, the grid and everything they pull in are for people who have
 * signed in. A visitor reading the sign-in form should not have to download
 * the data grid first: everything shipped as one 563 kB chunk before this.
 *
 * Fetched while the form is on screen rather than when it is submitted -- see
 * the effect below -- so the split buys a smaller first paint without paying
 * for it with a blank frame at the moment of arrival.
 */
const loadShell = () => import("@/components/app-shell")
const AppShell = lazy(async () => ({ default: (await loadShell()).AppShell }))

interface ConnectedState {
  api: TideApi
  connection: TideConnection
}

export default function App() {
  const [connected, setConnected] = useState<ConnectedState | null>(null)
  const [browserAuthentication, setBrowserAuthentication] =
    useState<TideBrowserAuthenticationInfo | null>(null)
  const [checkingIdentity, setCheckingIdentity] = useState(true)
  const [identityError, setIdentityError] = useState<string | null>(() => {
    const parameters = new URLSearchParams(window.location.search)
    return parameters.get("tide_auth_error") === "login_failed"
      ? "Sign-in could not be completed. Please try again."
      : null
  })
  const queryClient = useQueryClient()

  useEffect(() => {
    const controller = new AbortController()
    const parameters = new URLSearchParams(window.location.search)
    if (parameters.has("tide_auth_error")) {
      parameters.delete("tide_auth_error")
      const query = parameters.toString()
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
      )
    }

    async function restoreIdentity() {
      try {
        const authentication =
          await TideApi.discoverBrowserAuthentication(
            undefined,
            controller.signal,
          )
        setBrowserAuthentication(authentication)
        const api = await TideApi.restoreBrowserSession(
          authentication,
          undefined,
          controller.signal,
        )
        if (api !== null) {
          const connection = await api.connect(controller.signal)
          setConnected({ api, connection })
          setIdentityError(null)
        }
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return
        }
        setIdentityError(
          caught instanceof TideApiError
            ? caught.message
            : "The browser identity service could not be reached.",
        )
      } finally {
        if (!controller.signal.aborted) {
          setCheckingIdentity(false)
        }
      }
    }

    void restoreIdentity()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (connected === null) {
      return
    }
    // A session can end while someone is working -- the cookie expires, an
    // administrator disables the account, a password is reset. Without this
    // the next request fails wherever it happened to be made and the shell
    // stays up around it, showing an application the server no longer serves.
    return connected.api.onSessionExpired(() => {
      queryClient.clear()
      setConnected(null)
      setIdentityError("Your session has ended. Please sign in again.")
    })
  }, [connected, queryClient])

  // Named after the shell, not after whatever record was open when the
  // session ended: a sign-in form under an invoice number is a lie about what
  // is on screen.
  useDocumentTitle(connected ? null : SHELL_TITLE)

  useEffect(() => {
    if (!connected) {
      // Reading and typing take longer than this request; a failure is not
      // one, because `lazy` asks again when the shell is really needed.
      void loadShell().catch(() => undefined)
    }
  }, [connected])

  if (!connected) {
    return (
      <ConnectionScreen
        browserAuthentication={browserAuthentication}
        checkingIdentity={checkingIdentity}
        identityError={identityError}
        onConnected={(api, connection) => {
          setConnected({ api, connection })
        }}
      />
    )
  }

  return (
    <Suspense fallback={null}>
      <AppShell
        api={connected.api}
        connection={connected.connection}
        onDisconnect={() => {
          void (async () => {
            try {
              await connected.api.logout()
            } catch (caught) {
              setIdentityError(
                caught instanceof TideApiError
                  ? `The server session could not be ended: ${caught.message}`
                  : "The server session could not be ended. Please close this browser tab.",
              )
            } finally {
              queryClient.clear()
              setConnected(null)
            }
          })()
        }}
      />
    </Suspense>
  )
}
