# Web authentication

TIDE Web uses framework-owned username/password authentication by default. It
does not require a Microsoft account, social login, cloud identity tenant, or
other third-party login service. The optional OpenID Connect adapter remains
isolated for deployments that may choose it later; it is not required by the
framework or the Invoicing example.

Both modes become the same server-owned `Principal` and pass through the same
permissions, row policies, validation, concurrency, actions, reports, and audit
services. Neither mode gives React database credentials or lets it choose
roles.

## Local username/password flow

The first `start.bat web-demo` or `start.bat web` run creates a separate
`.tide/local-auth.sqlite3` identity store and securely prompts for the initial
`admin` password. The password is not echoed or written to `start.bat`, YAML,
the application database, or browser storage. Subsequent starts show the normal
Web sign-in form.

The local identity store is TIDE-owned and intentionally separate from both
managed application tables and legacy databases. It stores a unique random salt
and a versioned PBKDF2-HMAC-SHA-256 password hash, never the password. A local
user's roles are assigned by the developer/administrator command and must name
roles compiled from the application YAML; a login request cannot supply roles.

Create another user with the Windows shortcut:

```powershell
.\start.bat auth-user
```

Or use the cross-platform command and select the exact application roles:

```powershell
uv run tide auth create-user applications/invoicing `
  --store .tide/local-auth.sqlite3 `
  --username djordje `
  --display-name "Djordje Najdanovic" `
  --role sales_clerk `
  --role auditor
```

The command prompts twice without echoing the password. To replace it later:

```powershell
uv run tide auth set-password applications/invoicing `
  --store .tide/local-auth.sqlite3 `
  --username djordje
```

Refuse an account's sign-ins without deleting it, and put it back:

```powershell
uv run tide auth disable-user applications/invoicing `
  --store .tide/local-auth.sqlite3 --username djordje
uv run tide auth enable-user applications/invoicing `
  --store .tide/local-auth.sqlite3 --username djordje
```

Replace the roles an account holds. The list replaces rather than adds, so a
role can be withdrawn, and every name must be one the application compiled:

```powershell
uv run tide auth set-roles applications/invoicing `
  --store .tide/local-auth.sqlite3 --username djordje `
  --role sales_clerk
```

Disabling refuses new sign-ins immediately. A session already open ends at its
next revalidation, because the command runs in its own process and cannot reach
the server's memory; see below.

Local browser sessions are opaque, HTTP-only and time-bounded. React receives
only the cookie and a per-session CSRF proof. A small bounded failed-login
window slows repeated guessing, and every accepted identity is still
reauthorized by the ordinary service layer.

Where those sessions are kept depends on the database. A **managed** database
carries a `tide_browser_session` table, created by `--create-schema` alongside
the query-cursor and action-audit tables, and sessions go there: several TIDE
processes behind one address agree about who is signed in, and a restart no
longer signs everybody out. The failed-login counters share it, for a reason
worth stating plainly — a budget of five attempts held per process is really
five *per process*, so a second worker silently doubles it. A **legacy**
database gets neither table, because TIDE may not create one in a database it
does not own, and sessions stay in the process that issued them. The startup
banner says which is in force: `sessions: shared` or `sessions: this process`.

Only the digest of a session identifier is stored, never the identifier
itself, on the same reasoning as the query cursors: a backup or a replica of
the application database must not hand over every live session.

OIDC is deliberately excluded from the shared store and keeps its
single-process constraint. Its sessions hold the provider's access and refresh
tokens, and keeping those out of persistent application data is why its store
is process-local in the first place; sharing them means encrypting session
state at rest, which is separate work.

That constraint is enforced rather than described. Against a managed database
an OIDC server takes a lease in `tide_server_lease` and a second one refuses to
start, naming the incumbent and saying when its claim expires. And wherever
sessions stay in the process that issued them, the cookie carries an opaque
stamp naming that process, so a request that reaches a sibling is answered
`401 session_from_another_server` instead of a bare 401 -- the difference
between a diagnosable misconfiguration and users apparently being signed out at
random.

A live session is re-checked against the user store, by default every 30
seconds rather than on every request: re-reading costs a fresh SQLite
connection, a schema check and two queries — about 1.6ms, which is not a price
to pay per authenticated request. Within that interval the session keeps the
principal it was issued. At the next check, an account that has been deleted,
disabled, left with no permitted role, or given a different password loses its
sessions; a role merely added or removed reaches the principal without signing
the user out of work in progress. Requests that reach the revalidation boundary
together may both read the store, but the later one adopts the already-refreshed
session instead of treating the first request's replacement as a revocation.

A session records a digest of the password hash it was issued against — never
the hash, and never compared with anything a caller sends. That makes `tide
auth set-password` the operator's sign-out-everywhere: the store is a file both
the CLI and the server read, so changing a password ends that user's sessions
at the next check without touching the running process.

A successful sign-in also re-hashes the password when the stored work factor is
below the configured one, which is the only moment the plaintext is both in
hand and known good. That moves the hash without the password changing, so the
sign-in re-stamps that user's live sessions rather than signing them out for an
upgrade nobody asked for. The stored format carries its own iteration count, so
an old hash keeps verifying until it is replaced. Replacement is a conditional
compare-and-swap against the hash the login actually verified. If an
administrator resets the password in the meantime, the reset wins and the
stale login is refused; two simultaneous upgrades of the same password safely
converge on the one that reached storage first.

`LocalPasswordAuth.revoke_user` and `revoke_all` end sessions immediately, but
sessions live in the serving process's memory, so those are for embedded hosts
and a future authenticated admin route — not something a separate CLI process
can reach.

When a browser adapter is configured, every response carries a same-origin
content policy — `default-src`, `script-src`, `connect-src`, `img-src` and
`font-src` all `'self'`, `object-src 'none'`, `form-action 'self'`,
`frame-ancestors 'none'`, `base-uri 'self'`. `style-src` additionally permits
inline styles, because React writes element `style` attributes; the stylesheet
itself is same-origin. `Strict-Transport-Security` is sent only when the
request arrived over HTTPS: asserting it on a plain-HTTP loopback response
would at best be ignored and at worst pin a developer's machine to a scheme it
is not serving.

The identity store is restricted to its owning account when it is created —
`chmod 0600` where that means something, and `icacls` with inheritance dropped
on Windows, which is the documented primary platform and where `chmod` moves
only the read-only flag. The Windows grant names the SID reported by the current
process token, not `%USERNAME%`, which may belong to the interactive host rather
than a service, sandbox or impersonated process. TIDE checks that it can reopen
the file and restores inherited access on a failed hardening attempt. Both
platform mechanisms remain best effort: a failure leaves the file less
protected than intended rather than refusing to start, because turning a
hardening step into an outage helps nobody. SQLite writes journal side-files
beside the store and those inherit the *directory*, so restrict the directory
too if the store holds accounts you care about.

The session identifier and CSRF token do not rotate during a session. A fresh
pair is minted at every sign-in, which is where session fixation is prevented;
rotating mid-session would have to reach the response and the browser to be
useful, and that is a larger change than this one.

For a built same-origin Web renderer, start the local adapter explicitly:

```powershell
uv run --extra api --extra report --extra sqlserver tide serve `
  applications/invoicing --database-env `
  --auth local `
  --local-auth-store .tide/local-auth.sqlite3 `
  --web-root web/dist `
  --host 127.0.0.1 --port 8000
```

Non-loopback local-password serving requires TLS, either terminated here with
`--ssl-certfile` and `--ssl-keyfile` or declared upstream with
`--behind-tls-proxy`. The declaration is what makes the session cookie
`__Host-tide_session` with `Secure` set, since without it the server reads that
flag off a certificate a proxied deployment does not have. Which peers'
`X-Forwarded-*` headers are believed is the separate `--forwarded-allow-ips`
allowlist; see
[Behind a TLS-terminating reverse proxy](OPERATIONS.md#behind-a-tls-terminating-reverse-proxy).

### The cost of a refused sign-in

Verifying a password is deliberately expensive — 600,000 PBKDF2-HMAC-SHA-256
iterations — so a sign-in route is a CPU amplifier unless the server refuses
before paying. Two bounds keep that from being usable:

- **A throttled username is refused before hashing.** Once an identity has
  reached `max_failures` within the window, further attempts cost a dictionary
  lookup. The refusal was already decided; buying it a third of a second of CPU
  only helps the attacker. This does reveal that a username is currently
  throttled, which is something whoever caused the throttling already knows.
- **Concurrent verifications are capped** (`max_concurrent_verifications`,
  8 by default). Per-username throttling cannot see an attacker who never
  repeats a name, so the cost of verifying needs a bound that does not depend
  on identity. Exceeding it answers `503` with `Retry-After`, not `401` — a
  legitimate user should retry, not go and reset a password that is correct.

An un-throttled attempt for an unknown username still hashes a dummy value, so
a present identity cannot be told from an absent one by how long the answer
takes. That equalisation is why the first bound is conditional on throttling
rather than on whether the user exists.

### Administering accounts from the browser

The commands above stay the bootstrap: they create the first account, and they
are the way back in if every account that can administer has been locked out.
Everything after that can happen in the application, so withdrawing a role from
somebody who left does not need SSH.

Grant a role that carries `tide.users.administer`:

```powershell
uv run tide auth create-user applications/invoicing `
  --store .tide/local-auth.sqlite3 `
  --username admin --role administrator
```

That account gets an **Identities** entry beside the application's own
navigation. It lists the accounts with their roles and whether each may sign
in, and it lists the roles the application compiled with what each grants --
read-only, because a role is authored in YAML and compiled, never edited at
runtime. What the screen changes is assignment: the roles an account holds,
whether it may sign in, and its password.

The reference application grants `administrator` nothing else, so an account
holding only that role has no browse views at all and the browser opens on
Identities. That is deliberate: administering who may sign in is not a reason
to be able to read anything.

Two refusals are worth expecting, because both are the server's and neither is
the screen being careful on its own: an account may not be left with no roles,
and the last **enabled** account that can administer may not be demoted or
disabled. The message says which account it means.

Replacing a password ends every session that account has open. It requires no
knowledge of the previous one -- which is what makes it a reset, and why the
permission is one an application grants deliberately. See
[Administering identities](SECURITY.md#administering-identities).

## Optional browser OIDC flow

The optional browser flow uses Authorization Code with PKCE (`S256`):

1. React discovers only whether browser sign-in is available and the local
   login/session/logout paths.
2. FastAPI creates a short-lived, browser-bound state transaction and redirects
   to the provider.
3. FastAPI exchanges the returned code using the original PKCE verifier.
4. The existing OIDC/JWKS validator verifies the access token and maps only
   configured external roles to TIDE roles.
5. FastAPI retains access and optional refresh tokens in process memory and
   gives the browser only a random HTTP-only session cookie.
6. React retrieves a per-session CSRF value and supplies it on every mutating
   cookie-authenticated request. FastAPI reauthorizes the operation normally.

HTTPS deployments use `__Host-` cookies with `Secure`, `HttpOnly`, a root path,
and restrictive SameSite behavior. HTTP cookies are permitted only for an
explicit loopback callback used during local provider testing. Login state is
single-use, time-bounded, bound to an additional HTTP-only browser cookie, and
bounded in count. An invalid bearer header never falls back to a valid browser
cookie.

Refresh tokens, when the provider issues one, never enter JavaScript or browser
storage. FastAPI refreshes shortly before access-token expiry, validates the
replacement access token through the same OIDC adapter, requires the subject to
remain unchanged, and accepts provider refresh-token rotation. Failure ends the
local session rather than failing open.

## Identity-provider registration

Register a Web client with:

- Authorization Code enabled;
- PKCE `S256` supported;
- an exact callback such as
  `https://tide.example.com:8443/api/v1/_tide/browser-auth/callback`;
- access tokens issued as JWTs for the configured TIDE API audience;
- the external role claim used by `--oidc-role-claim`; and
- `offline_access` consent when refresh sessions are desired.

A confidential client can keep its secret in a server environment variable.
For a provider-approved public PKCE client, omit the secret option. Never place
client secrets, TLS-key passwords, access tokens, or refresh tokens in a batch
file or application YAML.

## Preflight a provider

Before opening a network listener, run the same configuration through TIDE's
read-only provider preflight. The flags intentionally match `tide serve` so a
reviewed configuration can be copied without translating names:

```powershell
$env:TIDE_WEB_CLIENT_SECRET = "replace-from-secret-store"

uv run --extra auth tide auth check-oidc applications/invoicing `
  --oidc-issuer https://identity.example.com/tenant `
  --oidc-audience tide-api `
  --oidc-role-map external-sales=sales_clerk `
  --web-oidc-client-id tide-web `
  --web-oidc-client-secret-env TIDE_WEB_CLIENT_SECRET `
  --web-oidc-redirect-uri `
    https://tide.example.com:8443/api/v1/_tide/browser-auth/callback `
  --web-oidc-scope openid `
  --web-oidc-scope profile `
  --web-oidc-scope offline_access
```

Add `--json` for a secret-free CI artifact. The command verifies the exact
HTTPS issuer and published JWKS URL, authorization and token endpoints,
authorization-code response/grant compatibility, PKCE `S256` when the provider
publishes its supported methods, the configured public (`none`) or confidential
(`client_secret_basic`) token authentication method, requested scopes, and
application role-map targets. It fails before server startup when published
metadata is incompatible.

These checks follow [OpenID Connect Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html),
[OAuth Authorization Server Metadata](https://www.rfc-editor.org/rfc/rfc8414.html),
and the current [OAuth security best practice](https://www.rfc-editor.org/rfc/rfc9700.html).
PKCE capability metadata is optional and some providers support S256 without
publishing that field. In that case preflight emits a warning, TIDE still sends
S256 and never downgrades, and reviewed provider documentation plus interactive
acceptance must confirm that the verifier is enforced. Preflight also cannot
prove that the client ID and exact callback were registered, that a real access
token carries the configured audience/role claim, or that the provider will
issue a refresh token. Complete the following interactive acceptance after
preflight:

1. Start the direct-TLS host with the same flags.
2. Choose **Sign in securely** and complete the provider's MFA/consent flow.
3. Confirm the expected TIDE principal, navigation, and role-limited actions.
4. Perform one permitted mutation to exercise the session's CSRF boundary.
5. Disconnect, refresh the page, and confirm that the local TIDE session ended.

Record the preflight JSON and this reviewed sign-in result with deployment
evidence; never record the client secret, authorization code, or tokens.

## Example direct-TLS host

Build the Web renderer first, set the confidential client secret in the server
environment, and replace every example value:

```powershell
cd web
npm ci
npm run build
cd ..

$env:TIDE_WEB_CLIENT_SECRET = "replace-from-secret-store"

uv run --extra api --extra auth --extra report --extra sqlserver tide serve `
  applications/invoicing --database-env `
  --auth oidc `
  --oidc-issuer https://identity.example.com/tenant `
  --oidc-audience tide-api `
  --oidc-role-map external-sales=sales_clerk `
  --web-root web/dist `
  --web-oidc-client-id tide-web `
  --web-oidc-client-secret-env TIDE_WEB_CLIENT_SECRET `
  --web-oidc-redirect-uri `
    https://tide.example.com:8443/api/v1/_tide/browser-auth/callback `
  --web-oidc-scope openid `
  --web-oidc-scope profile `
  --web-oidc-scope offline_access `
  --web-session-lifetime 28800 `
  --host 0.0.0.0 --port 8443 `
  --ssl-certfile C:\TIDE\tls\server-chain.pem `
  --ssl-keyfile C:\TIDE\tls\server-key.pem
```

Open the HTTPS application origin. The connection screen now offers **Sign in
securely**, returns to the application after provider authentication, restores
the browser session after a refresh, and ends the local session through the
existing **Disconnect** action.

Remote TUI, REST, and MCP clients continue to use OIDC bearer tokens. The
browser cookie is a same-origin Web transport and is not accepted by hosted
MCP authentication.

## Current operational boundary

The built-in session store intentionally holds tokens only in memory. It is
bounded, avoids token persistence in the application database, and makes a
process restart log every browser user out. Use one TIDE application process
for this initial contract. A reviewed encrypted shared-session adapter is
required before multiple workers or multiple application instances can share
browser sessions.

Because there is one process, the store's own locking decides how well it
serves concurrent users. The store-wide lock covers only the mapping — looking
a session up, evicting an expired one, ending one. Anything that can reach the
identity provider, which is a token refresh always and access-token
verification whenever signing keys must be fetched, runs under that session's
own lock instead. So a provider that is slow rather than broken delays the
sessions actually waiting on it, not every authenticated request in the
process. A sign-out that lands while the provider is answering still wins: the
store is asked again afterwards, and a session removed in the meantime is not
revived by the reply.

Disconnect currently ends the local TIDE session. Provider-wide single logout,
revocation calls, provider-specific consent behavior, encrypted session state
at rest (which is what OIDC needs before it can share a store), session-key
rotation, and trusted reverse-proxy deployment remain separate reviewed work.
Direct TLS and the controls in [Operational baseline](OPERATIONS.md) remain
required for the current non-loopback host.
