import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { AppShell } from "@/components/app-shell"
import { ConnectionScreen } from "@/components/connection-screen"
import type { TideApi } from "@/lib/api"
import type { TideConnection } from "@/lib/contracts"

interface ConnectedState {
  api: TideApi
  connection: TideConnection
}

export default function App() {
  const [connected, setConnected] = useState<ConnectedState | null>(null)
  const queryClient = useQueryClient()

  if (!connected) {
    return (
      <ConnectionScreen
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
        queryClient.clear()
        setConnected(null)
      }}
    />
  )
}
