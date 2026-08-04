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

Local browser sessions are opaque, HTTP-only, time-bounded and process-local.
React receives only the cookie and a per-session CSRF proof. A small bounded
failed-login window slows repeated guessing, and every accepted identity is
still reauthorized by the ordinary service layer. A server restart logs browser
users out but does not remove users or password hashes.

A live session is re-checked against the user store, by default every 30
seconds rather than on every request: re-reading costs a fresh SQLite
connection, a schema check and two queries — about 1.6ms, which is not a price
to pay per authenticated request. Within that interval the session keeps the
principal it was issued. At the next check, an account that has been deleted,
disabled, left with no permitted role, or given a different password loses its
sessions; a role merely added or removed reaches the principal without signing
the user out of work in progress.

That makes `tide auth set-password` the operator's sign-out-everywhere: the
store is a file both the CLI and the server read, so changing a password ends
that user's sessions at the next check without touching the running process.
`LocalPasswordAuth.revoke_user` and `revoke_all` end them immediately, but
sessions live in the serving process's memory, so those are for embedded hosts
and a future authenticated admin route — not something a separate CLI process
can reach.

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

Non-loopback local-password serving requires direct TLS with the existing
`--ssl-certfile` and `--ssl-keyfile` options. Reverse-proxy trust remains a
separate deployment feature.

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

Remote TUI, Qt, REST, and MCP clients continue to use OIDC bearer tokens. The
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
revocation calls, provider-specific consent behavior, shared session storage,
and trusted reverse-proxy deployment remain separate reviewed work. Direct TLS
and the controls in [Operational baseline](OPERATIONS.md) remain required for
the current non-loopback host.
