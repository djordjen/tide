# Web authentication

TIDE Web supports two deliberately separate connection modes:

- local `web-demo` and `web` shortcuts use a loopback-only development bearer
  token pasted into the current browser runtime; and
- reviewed deployments may enable same-origin browser sign-in through an
  OpenID Connect provider.

Both modes become the same server-owned `Principal` and pass through the same
permissions, row policies, validation, concurrency, actions, reports, and audit
services. Neither mode gives React database credentials or lets it choose
roles.

## Browser OIDC flow

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

Disconnect currently ends the local TIDE session. Provider-wide single logout,
revocation calls, provider-specific consent behavior, shared session storage,
and trusted reverse-proxy deployment remain separate reviewed work. Direct TLS
and the controls in [Operational baseline](OPERATIONS.md) remain required for
the current non-loopback host.
