import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, Plus, RefreshCw, ShieldCheck, UserRoundPlus } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { TideLine } from "@/components/tide-line"
import { sectionCaptionClass } from "@/components/form-field"
import { useDocumentTitle } from "@/lib/document-title"
import {
  DEFAULT_DATETIME_PATTERN,
  formatIsoDate,
} from "@/lib/format"
import { TideApiError, type TideApi } from "@/lib/api"
import { cn } from "@/lib/utils"

/**
 * Who holds which role, and who may sign in.
 *
 * Roles and what they grant are compiled from the application's metadata, so
 * this screen reports them and never offers to change them -- an authoring
 * change goes through the compiler like every other. What it administers is
 * assignment, which is the half that changes on a Tuesday because somebody
 * left.
 *
 * It is rendered only where `session.administration` is true, which means both
 * that this principal may administer and that this server owns identities to
 * administer. Where TIDE owns none the routes are not there at all.
 */

interface AdministrationWorkspaceProps {
  api: TideApi
  application: string
}

interface CreateDraft {
  username: string
  displayName: string
  password: string
  roles: string[]
}

const EMPTY_DRAFT: CreateDraft = {
  username: "",
  displayName: "",
  password: "",
  roles: [],
}

export function AdministrationWorkspace({
  api,
  application,
}: AdministrationWorkspaceProps) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<CreateDraft>(EMPTY_DRAFT)
  const [checkedRoles, setCheckedRoles] = useState<string[]>([])
  const [password, setPassword] = useState("")
  const [feedback, setFeedback] = useState<string | null>(null)

  useDocumentTitle(`Identities · ${application}`)

  const rolesQuery = useQuery({
    queryKey: ["administration", "roles"],
    queryFn: ({ signal }) => api.administrationRoles(signal),
  })
  const usersQuery = useQuery({
    queryKey: ["administration", "users"],
    queryFn: ({ signal }) => api.administrationUsers(signal),
  })

  const users = useMemo(() => usersQuery.data?.users ?? [], [usersQuery.data])
  const account = users.find((user) => user.username === selected) ?? null
  const roleNames = useMemo(
    () => (rolesQuery.data?.roles ?? []).map((role) => role.name),
    [rolesQuery.data],
  )

  // The checkboxes follow the selected account rather than being seeded once:
  // a saved change re-fetches the list, and the panel must agree with what
  // came back rather than with what was clicked.
  useEffect(() => {
    setCheckedRoles(account ? [...account.roles] : [])
    setPassword("")
  }, [account])

  function refreshed(message: string) {
    setFeedback(message)
    void queryClient.invalidateQueries({
      queryKey: ["administration", "users"],
    })
  }

  function refused(error: unknown) {
    setFeedback(
      error instanceof TideApiError
        ? error.message
        : "The change could not be saved.",
    )
  }

  const rolesMutation = useMutation({
    mutationFn: (roles: string[]) =>
      api.updateLocalUser(account?.username ?? "", { roles }),
    onSuccess: (changed) =>
      refreshed(`${changed.username} now holds ${changed.roles.join(", ")}.`),
    onError: refused,
  })

  const signInMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      api.updateLocalUser(account?.username ?? "", { enabled }),
    onSuccess: (changed) =>
      refreshed(
        changed.enabled
          ? `${changed.username} may sign in.`
          : `${changed.username} may no longer sign in.`,
      ),
    onError: refused,
  })

  const passwordMutation = useMutation({
    mutationFn: (value: string) =>
      api.resetLocalPassword(account?.username ?? "", value),
    onSuccess: () => {
      setPassword("")
      refreshed(
        `${account?.username} has a new password; their open sessions have ended.`,
      )
    },
    onError: refused,
  })

  const createMutation = useMutation({
    mutationFn: (values: CreateDraft) =>
      api.createLocalUser({
        username: values.username,
        password: values.password,
        roles: values.roles,
        ...(values.displayName ? { display_name: values.displayName } : {}),
      }),
    onSuccess: (created) => {
      setCreating(false)
      setDraft(EMPTY_DRAFT)
      setSelected(created.username)
      refreshed(`${created.username} can sign in now.`)
    },
    onError: refused,
  })

  const busy =
    rolesMutation.isPending ||
    signInMutation.isPending ||
    passwordMutation.isPending ||
    createMutation.isPending

  return (
    <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
      <div className="mb-5 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              Identities
            </h1>
            <TideLine className="w-12 text-primary/45" />
          </div>
          <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            Roles come from {application}. What is assigned here is who holds
            them.
          </p>
        </div>
      </div>

      {feedback ? (
        <p
          role="status"
          className="mb-4 rounded-lg border bg-card px-3 py-2 text-sm"
        >
          {feedback}
        </p>
      ) : null}

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm">
          <h2 className={sectionCaptionClass}>
            Accounts
            <span className="font-normal text-muted-foreground">
              {users.length ? `· ${users.length}` : ""}
            </span>
          </h2>

          {/* Above the table, where the reference application puts them: below
              it they would sit under a panel whose height changes with every
              account opened. */}
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5 md:px-5">
            <Button
              size="sm"
              onClick={() => {
                setCreating(true)
                setSelected(null)
                setFeedback(null)
              }}
            >
              <Plus />
              New account
            </Button>
            <Button
              aria-label="Refresh accounts"
              size="icon"
              variant="ghost"
              onClick={() =>
                void queryClient.invalidateQueries({
                  queryKey: ["administration"],
                })
              }
            >
              <RefreshCw />
            </Button>
            {usersQuery.data?.truncated ? (
              <span className="text-xs text-muted-foreground">
                Showing the first {users.length} accounts.
              </span>
            ) : null}
          </div>

          <div className="overflow-x-auto">
            <table aria-label="Accounts" className="w-full text-sm">
              <thead className="bg-muted/40 text-left text-xs text-muted-foreground uppercase">
                <tr>
                  <th className="px-4 py-2 font-semibold md:px-5">Account</th>
                  <th className="px-4 py-2 font-semibold">Roles</th>
                  <th className="px-4 py-2 font-semibold">Sign-in</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {users.map((user) => (
                  <tr
                    key={user.username}
                    className={cn(
                      "align-top",
                      user.username === selected ? "bg-accent/40" : null,
                    )}
                  >
                    <td className="px-4 py-2 md:px-5">
                      <button
                        type="button"
                        className="rounded font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/40"
                        onClick={() => {
                          setCreating(false)
                          setSelected(user.username)
                          setFeedback(null)
                        }}
                      >
                        {user.username}
                      </button>
                      <p className="text-xs text-muted-foreground">
                        {user.display_name}
                      </p>
                    </td>
                    <td className="px-4 py-2">
                      <span className="flex flex-wrap gap-1">
                        {user.roles.map((role) => (
                          <Badge key={role} variant="outline">
                            {role}
                          </Badge>
                        ))}
                      </span>
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      {user.enabled ? "Enabled" : "Disabled"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {creating ? (
            <div className="border-t">
              <h2 className={sectionCaptionClass}>
                <UserRoundPlus className="size-4" />
                New account
              </h2>
              <div className="space-y-3 px-4 py-4 md:px-5">
                <LabelledInput
                  label="Username"
                  value={draft.username}
                  onChange={(value) =>
                    setDraft((current) => ({ ...current, username: value }))
                  }
                />
                <LabelledInput
                  label="Display name"
                  value={draft.displayName}
                  onChange={(value) =>
                    setDraft((current) => ({ ...current, displayName: value }))
                  }
                />
                <LabelledInput
                  label="Password"
                  type="password"
                  value={draft.password}
                  onChange={(value) =>
                    setDraft((current) => ({ ...current, password: value }))
                  }
                />
                <RoleChoices
                  roles={roleNames}
                  checked={draft.roles}
                  onToggle={(role) =>
                    setDraft((current) => ({
                      ...current,
                      roles: toggled(current.roles, role),
                    }))
                  }
                />
                <div className="flex gap-2">
                  <Button
                    disabled={busy}
                    size="sm"
                    onClick={() => createMutation.mutate(draft)}
                  >
                    Create account
                  </Button>
                  <Button
                    disabled={busy}
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setCreating(false)
                      setDraft(EMPTY_DRAFT)
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

          {account ? (
            <div className="border-t">
              <h2 className={sectionCaptionClass}>
                {account.username}
                <span className="font-normal text-muted-foreground">
                  · {account.display_name}
                </span>
              </h2>
              <div className="space-y-4 px-4 py-4 md:px-5">
                <RoleChoices
                  roles={roleNames}
                  checked={checkedRoles}
                  onToggle={(role) =>
                    setCheckedRoles((current) => toggled(current, role))
                  }
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={busy || checkedRoles.length === 0}
                    size="sm"
                    onClick={() => rolesMutation.mutate([...checkedRoles].sort())}
                  >
                    Save roles
                  </Button>
                  <Button
                    disabled={busy}
                    size="sm"
                    variant="outline"
                    onClick={() => signInMutation.mutate(!account.enabled)}
                  >
                    {account.enabled ? "Disable account" : "Enable account"}
                  </Button>
                </div>

                <div className="border-t pt-4">
                  <p className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                    <KeyRound className="size-3.5 text-muted-foreground" />
                    Password
                  </p>
                  <p className="mb-2 text-xs text-muted-foreground">
                    Replacing it ends every session this account has open. Last
                    changed{" "}
                    {formatIsoDate(
                      account.password_changed_at,
                      DEFAULT_DATETIME_PATTERN,
                    )}
                    .
                  </p>
                  <div className="flex flex-wrap items-end gap-2">
                    <LabelledInput
                      className="w-full sm:w-64"
                      label="New password"
                      type="password"
                      value={password}
                      onChange={setPassword}
                    />
                    <Button
                      disabled={busy || password.length === 0}
                      size="sm"
                      variant="outline"
                      onClick={() => passwordMutation.mutate(password)}
                    >
                      Reset password
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </section>

        <section
          aria-label="Roles"
          className="h-fit min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm"
        >
          <h2 className={sectionCaptionClass}>Roles</h2>
          <div className="divide-y">
            {(rolesQuery.data?.roles ?? []).map((role) => (
              <div className="px-4 py-3 md:px-5" key={role.name}>
                <p className="text-sm font-medium">{role.name}</p>
                <p className="mt-1 text-xs break-words text-muted-foreground">
                  {role.grants.length
                    ? role.grants.join(", ")
                    : "grants nothing"}
                </p>
              </div>
            ))}
          </div>
          <p className="border-t px-4 py-3 text-xs text-muted-foreground md:px-5">
            Declared by the application and compiled. Changing what a role
            grants is an authoring change.
          </p>
        </section>
      </div>
    </main>
  )
}

function toggled(roles: string[], role: string): string[] {
  return roles.includes(role)
    ? roles.filter((name) => name !== role)
    : [...roles, role]
}

function RoleChoices({
  roles,
  checked,
  onToggle,
}: {
  roles: string[]
  checked: string[]
  onToggle: (role: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-2">
      {roles.map((role) => (
        <label
          className="flex items-center gap-2 text-sm"
          key={role}
        >
          <input
            checked={checked.includes(role)}
            className="size-4 accent-primary"
            onChange={() => onToggle(role)}
            type="checkbox"
          />
          {role}
        </label>
      ))}
    </div>
  )
}

function LabelledInput({
  className,
  label,
  onChange,
  type,
  value,
}: {
  className?: string
  label: string
  onChange: (value: string) => void
  type?: string
  value: string
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <label className="mb-1 block text-sm font-medium text-muted-foreground">
        {label}
        <Input
          className="mt-1"
          onChange={(event) => onChange(event.target.value)}
          type={type}
          value={value}
        />
      </label>
    </div>
  )
}
