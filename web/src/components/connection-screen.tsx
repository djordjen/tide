import { useState, type FormEvent } from "react"
import { ArrowRight, DatabaseZap, LockKeyhole, Waves } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { TideApi, TideApiError } from "@/lib/api"
import type {
  TideBrowserAuthenticationInfo,
  TideConnection,
} from "@/lib/contracts"

interface ConnectionScreenProps {
  browserAuthentication: TideBrowserAuthenticationInfo | null
  checkingIdentity: boolean
  identityError: string | null
  onConnected: (api: TideApi, connection: TideConnection) => void
}

export function ConnectionScreen({
  browserAuthentication,
  checkingIdentity,
  identityError,
  onConnected,
}: ConnectionScreenProps) {
  const [token, setToken] = useState("")
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setConnecting(true)
    setError(null)
    try {
      const api = new TideApi(token)
      const connection = await api.connect()
      onConnected(api, connection)
    } catch (caught) {
      setError(
        caught instanceof TideApiError
          ? caught.message
          : "The application connection failed.",
      )
    } finally {
      setConnecting(false)
    }
  }

  return (
    <main className="relative flex min-h-screen overflow-hidden bg-background">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_15%_15%,color-mix(in_oklab,var(--color-primary)_13%,transparent),transparent_34%),radial-gradient(circle_at_88%_8%,color-mix(in_oklab,var(--color-chart-2)_12%,transparent),transparent_27%)]" />
      <div className="relative mx-auto grid w-full max-w-7xl items-center gap-14 px-6 py-10 lg:grid-cols-[1.15fr_0.85fr] lg:px-12">
        <section className="hidden max-w-2xl lg:block">
          <div className="mb-8 flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <Waves className="size-7" />
          </div>
          <p className="mb-4 text-sm font-semibold tracking-[0.22em] text-primary uppercase">
            TIDE Framework
          </p>
          <h1 className="text-balance text-5xl leading-[1.08] font-semibold tracking-tight text-foreground">
            Business applications,
            <span className="block text-muted-foreground">
              shaped by metadata.
            </span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-muted-foreground">
            One secured application model, rendered consistently across Web,
            desktop and terminal experiences.
          </p>
          <div className="mt-10 grid max-w-xl grid-cols-3 gap-4">
            <Feature
              icon={DatabaseZap}
              label="Server owned"
              detail="Every query stays behind FastAPI."
            />
            <Feature
              icon={LockKeyhole}
              label="Capability aware"
              detail="The UI sees only permitted metadata."
            />
            <Feature
              icon={Waves}
              label="Renderer neutral"
              detail="YAML remains the shared definition."
            />
          </div>
        </section>

        <section className="mx-auto w-full max-w-md rounded-3xl border border-border/80 bg-card/95 p-7 shadow-2xl shadow-slate-950/8 backdrop-blur sm:p-9">
          <div className="mb-8 lg:hidden">
            <div className="mb-4 flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Waves className="size-5" />
            </div>
            <p className="text-xs font-semibold tracking-[0.2em] text-primary uppercase">
              TIDE Framework
            </p>
          </div>
          <p className="text-sm font-medium text-primary">Web renderer</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">
            {browserAuthentication?.enabled
              ? "Sign in to your application"
              : "Connect to an application"}
          </h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {browserAuthentication?.enabled ? (
              <>
                Continue through your organization&apos;s identity provider. TIDE
                keeps provider tokens behind the secured application server.
              </>
            ) : (
              <>
                Paste the temporary token printed by{" "}
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs text-foreground">
                  start.bat web-demo
                </code>
                . It remains only in this browser tab&apos;s memory.
              </>
            )}
          </p>

          <div className="mt-7 space-y-5">
            {identityError || error ? (
              <div
                id="connection-error"
                role="alert"
                className="rounded-xl border border-destructive/25 bg-destructive/8 px-3 py-2.5 text-sm text-destructive"
              >
                {error ?? identityError}
              </div>
            ) : null}
            {checkingIdentity ? (
              <Button className="w-full" size="lg" disabled>
                Checking secure session…
              </Button>
            ) : browserAuthentication?.enabled &&
              browserAuthentication.login_path !== null ? (
              <Button className="w-full" size="lg" asChild>
                <a href={browserAuthentication.login_path}>
                  Sign in securely
                  <ArrowRight />
                </a>
              </Button>
            ) : (
              <form className="space-y-5" onSubmit={connect}>
                <div className="space-y-2">
                  <label
                    className="text-sm font-medium text-foreground"
                    htmlFor="api-token"
                  >
                    Application token
                  </label>
                  <Input
                    id="api-token"
                    name="api-token"
                    type="password"
                    autoComplete="off"
                    autoFocus
                    spellCheck={false}
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="Paste token"
                    aria-describedby={error ? "connection-error" : undefined}
                  />
                </div>
                <Button
                  className="w-full"
                  size="lg"
                  type="submit"
                  disabled={connecting || !token.trim()}
                >
                  {connecting ? "Connecting…" : "Connect securely"}
                  {!connecting ? <ArrowRight /> : null}
                </Button>
              </form>
            )}
          </div>

          <p className="mt-6 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            <LockKeyhole className="mt-0.5 size-3.5 shrink-0" />
            The browser receives no database URL, SQL access, raw YAML or
            permission expressions.
          </p>
        </section>
      </div>
    </main>
  )
}

function Feature({
  icon: Icon,
  label,
  detail,
}: {
  icon: typeof Waves
  label: string
  detail: string
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-card/60 p-4 backdrop-blur">
      <Icon className="mb-3 size-5 text-primary" />
      <p className="text-sm font-semibold">{label}</p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
    </div>
  )
}
