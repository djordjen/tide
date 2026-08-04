import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { AppShell } from "@/components/app-shell"
import { ConnectionScreen } from "@/components/connection-screen"
import { TideApi, TideApiError } from "@/lib/api"
import type {
  TideBrowserAuthenticationInfo,
  TideConnection,
} from "@/lib/contracts"

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
  )
}
