import { lazy, Suspense, useMemo, useState } from "react"
import {
  Boxes,
  ChevronDown,
  CircleUserRound,
  FileText,
  House,
  LogOut,
  Menu,
  Moon,
  Search,
  ShieldCheck,
  Sun,
  UsersRound,
  Waves,
} from "lucide-react"

import { BrowseWorkspace } from "@/components/browse-workspace"
import { HomeDashboard } from "@/components/home-dashboard"
import { GlobalSearch } from "@/components/global-search"
import { Button } from "@/components/ui/button"
import { TideLine } from "@/components/tide-line"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { TideApi } from "@/lib/api"
import type { TideConnection } from "@/lib/contracts"
import { useUrlParameter } from "@/lib/url-state"

/**
 * Identity administration, loaded when it is opened rather than when the
 * application is. It is framework chrome and most sessions never open it.
 */
const AdministrationWorkspace = lazy(async () => ({
  default: (await import("@/components/administration-workspace"))
    .AdministrationWorkspace,
}))

/** Leaving a view closes whatever record was open in it. */
const CLEARED_BY_VIEW = ["record"] as const

/**
 * The destinations that are not application views.
 *
 * They travel in the same `view` parameter, under names the manifest can
 * never hold: a view name is a compiled identifier and cannot begin with an
 * underscore, so these can never collide with one and need no second
 * parameter to keep them apart. Home is the default landing, so it is the
 * parameter's fallback and keeps the clean URL.
 */
const TIDE_ADMINISTRATION = "_tide.administration"
const TIDE_HOME = "_tide.home"

/**
 * Where a navigation stops being read and starts being hunted through.
 *
 * Under this many entries the whole list is on the screen at once and a
 * filter box is a control in the way of it. The reference application this
 * renderer is measured against carries several times this many.
 */
const NAVIGATION_FILTER_MINIMUM = 10

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
  const [requestedView, setSelectedView] = useUrlParameter(
    "view",
    firstView !== undefined ? TIDE_HOME : "",
    CLEARED_BY_VIEW,
  )
  // True only where this principal may administer identities and this server
  // owns some; the routes behind it are absent rather than denied elsewhere.
  const canAdminister = connection.session.administration === true
  // An identity whose only capability is administering has no navigation at
  // all -- the reference application grants that role nothing else -- so with
  // no views to show, this is where the shell opens.
  const administering =
    canAdminister &&
    (requestedView === TIDE_ADMINISTRATION || firstView === undefined)
  // The address bar is a caller like any other, so a view it names is checked
  // against the manifest rather than trusted. A principal who cannot see the
  // view in a link they were sent gets their own landing, not an empty shell.
  const homeActive =
    !administering &&
    firstView !== undefined &&
    (requestedView === TIDE_HOME ||
      connection.presentation.views[requestedView] === undefined)
  const selectedView = administering
    ? TIDE_ADMINISTRATION
    : homeActive
      ? TIDE_HOME
      : requestedView
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  )
  // A one-shot instruction from a Home tile: open this browse with this
  // saved view applied. Cleared whenever any other navigation happens, so
  // returning to the view later opens it plain.
  const [pendingSavedView, setPendingSavedView] = useState<{
    view: string
    name: string
  } | null>(null)
  function openView(name: string) {
    setPendingSavedView(null)
    setSelectedView(name)
  }
  function openSavedView(viewName: string, name: string) {
    setPendingSavedView({ view: viewName, name })
    setSelectedView(viewName)
  }
  const view =
    administering || homeActive
      ? undefined
      : connection.presentation.views[selectedView]
  const form =
    view?.detail_view !== null && view?.detail_view !== undefined
      ? (connection.presentation.forms[view.detail_view] ?? null)
      : null
  const allItems = useMemo(
    () =>
      connection.presentation.navigation.flatMap((group) => group.items),
    [connection.presentation.navigation],
  )
  const [navigationFilter, setNavigationFilter] = useState("")
  const filterable = allItems.length >= NAVIGATION_FILTER_MINIMUM
  const navigationGroups = useMemo(() => {
    const needle = navigationFilter.trim().toLowerCase()
    if (!needle) {
      return connection.presentation.navigation
    }
    return connection.presentation.navigation
      .map((group) => ({
        ...group,
        items: group.items.filter((item) =>
          item.label.toLowerCase().includes(needle),
        ),
      }))
      // A heading over nothing is a promise the list is not keeping.
      .filter((group) => group.items.length > 0)
  }, [connection.presentation.navigation, navigationFilter])

  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light"
    document.documentElement.classList.toggle("dark", next === "dark")
    window.localStorage.setItem("tide.web.theme", next)
    setTheme(next)
  }

  if (!administering && !firstView) {
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
            <p className="font-display truncate text-sm font-semibold">
              TIDE Framework
            </p>
            {/* Which application, and which build of it: the manifest
                carries the version, so asking costs nobody a terminal. */}
            <p className="truncate text-xs text-sidebar-foreground/55">
              {connection.presentation.application} · v
              {connection.presentation.application_version}
            </p>
          </div>
        </div>
        <TideLine className="mb-1 ml-5 w-14 text-sidebar-primary/70" />
        <Separator className="bg-sidebar-border" />

        {filterable ? (
          // Outside the scrolling list on purpose: the box a long list is
          // being narrowed with has to stay where it was typed into.
          <div className="px-3 pt-4">
            <div className="relative">
              <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-sidebar-foreground/40" />
              <input
                aria-label="Filter navigation"
                className="h-9 w-full rounded-lg border border-sidebar-border bg-sidebar-accent/40 pr-3 pl-9 text-sm outline-none placeholder:text-sidebar-foreground/40 focus:ring-2 focus:ring-sidebar-ring"
                placeholder="Filter views"
                value={navigationFilter}
                onChange={(event) =>
                  setNavigationFilter(event.target.value)
                }
              />
            </div>
          </div>
        ) : null}

        <nav
          aria-label="Application navigation"
          className={cn(
            "flex-1 overflow-y-auto px-3 pb-5",
            filterable ? "pt-4" : "pt-5",
          )}
        >
          <div className="mb-6 space-y-1">
            <button
              type="button"
              className={cn(
                "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                homeActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/55 hover:text-sidebar-accent-foreground",
              )}
              onClick={() => openView(TIDE_HOME)}
            >
              <House
                className={cn(
                  "size-4",
                  homeActive
                    ? "text-sidebar-primary"
                    : "text-sidebar-foreground/45",
                )}
              />
              <span>Home</span>
            </button>
          </div>
          {navigationGroups.length === 0 ? (
            <p className="px-2 text-sm text-sidebar-foreground/45">
              No matching views
            </p>
          ) : null}
          {navigationGroups.map((group) => (
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
                      onClick={() => openView(item.view)}
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

        {canAdminister ? (
          // Framework chrome, not application navigation: the manifest carries
          // the application's own destinations and this is not one of them, so
          // it sits with the identity it belongs beside rather than inside a
          // group the application named.
          <div className="border-t border-sidebar-border px-3 py-2">
            <button
              type="button"
              className={cn(
                "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                administering
                  ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/55 hover:text-sidebar-accent-foreground",
              )}
              onClick={() => openView(TIDE_ADMINISTRATION)}
            >
              <UsersRound
                className={cn(
                  "size-4",
                  administering
                    ? "text-sidebar-primary"
                    : "text-sidebar-foreground/45",
                )}
              />
              <span>Identities</span>
            </button>
          </div>
        ) : null}

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
            {/* Native on purpose, and only below `lg`. This is the phone
                navigation, where a native select opens the platform's own
                picker -- a wheel you can thumb -- and a listbox is a
                scrolling div. It already wears the same surface as the
                code-owned controls: `appearance-none` plus the chevron
                beside it, so nothing about it reads as foreign. */}
            <select
              aria-label="Current workspace"
              className="h-9 w-full appearance-none rounded-lg border bg-background pr-9 pl-3 text-sm font-medium outline-none focus:ring-2 focus:ring-ring/25"
              value={selectedView}
              onChange={(event) => openView(event.target.value)}
            >
              <option value={TIDE_HOME}>Home</option>
              {allItems.map((item) => (
                <option key={item.view} value={item.view}>
                  {item.label}
                </option>
              ))}
              {canAdminister ? (
                <option value={TIDE_ADMINISTRATION}>Identities</option>
              ) : null}
            </select>
            <ChevronDown className="pointer-events-none absolute top-2.5 right-3 size-4 text-muted-foreground" />
          </div>
          <div className="hidden min-w-0 flex-1 items-center gap-2 lg:flex">
            <Boxes className="size-4 text-muted-foreground" />
            <p className="truncate text-sm text-muted-foreground">
              {connection.presentation.application}
              <span className="mx-2 text-border">/</span>
              <span className="font-medium text-foreground">
                {view?.label ?? (administering ? "Identities" : "Home")}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-1">
            <div className="mr-2 hidden items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[0.68rem] font-medium text-muted-foreground sm:flex">
              <span className="size-1.5 rounded-full bg-emerald-500" />
              Secured API
            </div>
            <GlobalSearch
              api={api}
              views={connection.presentation.views}
            />
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

        {homeActive ? (
          <HomeDashboard
            api={api}
            presentation={connection.presentation}
            principal={connection.session.principal}
            onOpenView={openView}
            onOpenSavedView={openSavedView}
          />
        ) : administering || !view ? (
          <Suspense fallback={null}>
            <AdministrationWorkspace
              api={api}
              application={connection.presentation.application}
            />
          </Suspense>
        ) : (
          <BrowseWorkspace
            key={view.view}
            api={api}
            application={connection.presentation.application}
            principal={connection.session.principal}
            view={view}
            form={form}
            forms={connection.presentation.forms}
            views={connection.presentation.views}
            reports={connection.presentation.reports ?? {}}
            audit={
              connection.session.entities[view.entity]?.audit === true
            }
            initialSavedView={
              pendingSavedView?.view === view.view
                ? pendingSavedView.name
                : null
            }
          />
        )}
      </section>
    </div>
  )
}
