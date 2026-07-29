import { useMemo, useState } from "react"
import {
  Boxes,
  ChevronDown,
  CircleUserRound,
  FileText,
  LogOut,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
  Waves,
} from "lucide-react"

import { BrowseWorkspace } from "@/components/browse-workspace"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { TideApi } from "@/lib/api"
import type { TideConnection } from "@/lib/contracts"

interface AppShellProps {
  api: TideApi
  connection: TideConnection
  onDisconnect: () => void
}

export function AppShell({
  api,
  connection,
  onDisconnect,
}: AppShellProps) {
  const firstView = connection.presentation.navigation[0]?.items[0]?.view
  const [selectedView, setSelectedView] = useState(firstView ?? "")
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  )
  const view = connection.presentation.views[selectedView]
  const form =
    view?.detail_view !== null && view?.detail_view !== undefined
      ? (connection.presentation.forms[view.detail_view] ?? null)
      : null
  const allItems = useMemo(
    () =>
      connection.presentation.navigation.flatMap((group) => group.items),
    [connection.presentation.navigation],
  )

  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light"
    document.documentElement.classList.toggle("dark", next === "dark")
    window.localStorage.setItem("tide.web.theme", next)
    setTheme(next)
  }

  if (!firstView || !view) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="max-w-md rounded-2xl border bg-card p-8 text-center shadow-sm">
          <ShieldCheck className="mx-auto size-9 text-muted-foreground" />
          <h1 className="mt-4 text-xl font-semibold">
            No available workspaces
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            This identity authenticated successfully, but it has no accessible
            browse views in the application navigation.
          </p>
          <Button className="mt-6" variant="outline" onClick={onDisconnect}>
            Disconnect
          </Button>
        </div>
      </main>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-muted/30">
      <aside className="hidden w-66 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex">
        <div className="flex h-17 items-center gap-3 px-5">
          <div className="flex size-9 items-center justify-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
            <Waves className="size-4.5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">TIDE Framework</p>
            <p className="truncate text-xs text-sidebar-foreground/55">
              {connection.presentation.application}
            </p>
          </div>
        </div>
        <Separator className="bg-sidebar-border" />

        <nav
          aria-label="Application navigation"
          className="flex-1 overflow-y-auto px-3 py-5"
        >
          {connection.presentation.navigation.map((group) => (
            <div className="mb-6" key={group.label}>
              <p className="mb-2 px-2 text-[0.68rem] font-semibold tracking-[0.13em] text-sidebar-foreground/45 uppercase">
                {group.label}
              </p>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = item.view === selectedView
                  return (
                    <button
                      key={item.view}
                      type="button"
                      className={cn(
                        "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                        active
                          ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                          : "text-sidebar-foreground/70 hover:bg-sidebar-accent/55 hover:text-sidebar-accent-foreground",
                      )}
                      onClick={() => setSelectedView(item.view)}
                    >
                      <FileText
                        className={cn(
                          "size-4",
                          active
                            ? "text-sidebar-primary"
                            : "text-sidebar-foreground/45",
                        )}
                      />
                      <span className="truncate">{item.label}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center gap-3 rounded-xl px-3 py-2.5">
            <CircleUserRound className="size-8 shrink-0 text-sidebar-foreground/50" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">
                {connection.session.principal}
              </p>
              <p className="truncate text-[0.68rem] text-sidebar-foreground/45">
                {connection.session.roles.join(", ") || "Authenticated"}
              </p>
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label="Disconnect"
                  className="text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  size="icon"
                  variant="ghost"
                  onClick={onDisconnect}
                >
                  <LogOut />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Disconnect</TooltipContent>
            </Tooltip>
          </div>
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-17 shrink-0 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur md:px-6">
          <Menu className="size-5 text-muted-foreground lg:hidden" />
          <div className="relative min-w-0 flex-1 lg:hidden">
            <select
              aria-label="Current workspace"
              className="h-9 w-full appearance-none rounded-lg border bg-background pr-9 pl-3 text-sm font-medium outline-none focus:ring-2 focus:ring-ring/25"
              value={selectedView}
              onChange={(event) => setSelectedView(event.target.value)}
            >
              {allItems.map((item) => (
                <option key={item.view} value={item.view}>
                  {item.label}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute top-2.5 right-3 size-4 text-muted-foreground" />
          </div>
          <div className="hidden min-w-0 flex-1 items-center gap-2 lg:flex">
            <Boxes className="size-4 text-muted-foreground" />
            <p className="truncate text-sm text-muted-foreground">
              {connection.presentation.application}
              <span className="mx-2 text-border">/</span>
              <span className="font-medium text-foreground">{view.label}</span>
            </p>
          </div>
          <div className="flex items-center gap-1">
            <div className="mr-2 hidden items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[0.68rem] font-medium text-muted-foreground sm:flex">
              <span className="size-1.5 rounded-full bg-emerald-500" />
              Secured API
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label={`Use ${theme === "light" ? "dark" : "light"} theme`}
                  size="icon"
                  variant="ghost"
                  onClick={toggleTheme}
                >
                  {theme === "light" ? <Moon /> : <Sun />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                Use {theme === "light" ? "dark" : "light"} theme
              </TooltipContent>
            </Tooltip>
            <Button
              className="lg:hidden"
              size="icon"
              variant="ghost"
              aria-label="Disconnect"
              onClick={onDisconnect}
            >
              <LogOut />
            </Button>
          </div>
        </header>

        <BrowseWorkspace
          key={view.view}
          api={api}
          application={connection.presentation.application}
          principal={connection.session.principal}
          view={view}
          form={form}
          forms={connection.presentation.forms}
        />
      </section>
    </div>
  )
}
