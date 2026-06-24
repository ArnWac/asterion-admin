# OAuth / OIDC sign-in

The `auth_oauth` extension adds "Sign in with Google" — and, with additional
provider classes, any other OIDC identity provider — to the admin UI. This
document walks through the sign-in flow, setup, the auto-create policy, the
security properties the extension enforces, how to add another provider, and how
to disable it.

## The sign-in flow

```text
Browser                       asterion                              Google
  │  click "Sign in with Google"
  │  GET /api/v1/oauth/google/login
  │ ────────────────────────► │
  │                           │ generate state + nonce + PKCE
  │                           │ seal them into an HttpOnly cookie
  │ ◄──────── 302 ─────────── │ Location: accounts.google.com/...
  │  follow redirect ───────────────────────────────────────────► │
  │                                                                │ user consents
  │ ◄────── 302 ─── /api/v1/oauth/google/callback?code=…&state=… ──│
  │  follow redirect          │
  │ ────────────────────────► │ unseal cookie, verify state
  │                           │ POST oauth2.googleapis.com/token
  │                           │      (with PKCE code_verifier)  ──► │
  │                           │ ◄────── id_token + access_token ───│
  │                           │ fetch JWKS (cached), verify
  │                           │ map claims → ExternalIdentityData
  │                           │ find_or_create user
  │                           │ mint framework JWT
  │ ◄──── 302 ─── /admin/login-complete#token=<jwt>&return_to=… ───│
  │  follow redirect          │
  │ ────────────────────────► │ static HTML + JS
  │  JS reads #token, stores  │
  │  in localStorage,         │
  │  redirects to return_to   │
```

The user lands on `/admin/dashboard` (or the path they were heading for before
being bounced to login) with a valid framework JWT in `localStorage` — the same
key the password-login flow populates.

## Setup

### 1. Register the OAuth client at Google

In the Google Cloud console:

* **OAuth client type:** Web application.
* **Authorized redirect URIs:** your callback URL exactly —
  `https://your-app.example.com/api/v1/oauth/google/callback`.
* For local dev, `http://localhost:8000/api/v1/oauth/google/callback` works on
  `http://localhost` (Google permits it as a special case; HTTPS for any other
  host).

Copy the `client_id` and `client_secret` — the extension needs both.

### 2. Wire the extension into create_admin

```python
import os
from asterion import create_admin, CoreAdminConfig
from asterion.extensions.auth_oauth import OAuthExtension, GoogleOIDCProvider

app = create_admin(
    config=CoreAdminConfig.from_env(),
    extensions=[
        OAuthExtension(
            providers=[
                GoogleOIDCProvider(
                    client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                    client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
                    # Optional: restrict to a Google Workspace domain.
                    extra_authorize_params={"hd": "acme.example"},
                ),
            ],
            auto_create_users=False,   # see § Auto-create before enabling
        ),
    ],
)
```

### 3. Generate the ExternalIdentity migration

The framework ships no migration for the extension's table — see
[Extensions § Migrations for extension tables](extensions.md#migrations-for-extension-tables).
In your project, ensure the shared `env.py` imports the extension so
autogenerate sees its tables:

```python
import asterion.extensions.auth_oauth  # noqa: F401  (near the top of env.py)
```

```bash
alembic -c alembic_shared.ini revision --autogenerate -m "add external_identities"
alembic -c alembic_shared.ini upgrade head
```

### 4. (Optional) Wire a custom user provider

The default `BuiltinOAuthUserProvider` operates on the framework's `User` table.
If your app uses an external identity store (LDAP, an existing user table, an
IAM service), implement the `OAuthCapableUserProvider` Protocol and pass it in:

```python
from asterion.extensions.auth_oauth import OAuthCapableUserProvider

class MyOAuthUserProvider:
    async def find_or_create_by_external_identity(self, **kwargs):
        ...

OAuthExtension(providers=[...], user_provider=MyOAuthUserProvider())
```

See
[Auth architecture § Optional capabilities](auth-architecture.md#optional-capabilities-oauthcapableuserprovider).

## Auto-create

`auto_create_users` defaults to `False` — only pre-provisioned users (those who
already have an `ExternalIdentity` row pointing at them) can sign in. This is the
safe default for most deployments: an operator invites people explicitly, who
then sign in via OAuth and inherit the existing account.

Setting `auto_create_users=True` lets the callback create a fresh `User` +
`ExternalIdentity` pair when an unknown subject signs in. The
`BuiltinOAuthUserProvider` then enforces three rules:

1. **`email_verified` must be `True`** in the IdP claims. Google and most major
   IdPs send it; providers that don't are refused.
2. **No silent linking by email.** If a `User` with the same email already
   exists (e.g. from password signup), the flow refuses rather than auto-linking
   — otherwise an attacker who registers a Google account with a victim's email
   could take over the account. Account linking should be an explicit flow that
   authenticates both factors, which this version does not ship.
3. **Inactive users can't sign in.** A linked identity whose backing user is
   deactivated returns `401`, matching password-login behavior.

A custom user provider can override any of these by implementing the Protocol
with different rules.

## Security properties

| Threat | Defence |
|---|---|
| CSRF on the callback | Per-flow `state` sealed into an HttpOnly cookie; mismatch → 302 to `/admin/login?oauth_error=state_mismatch` |
| Authorization-code interception | PKCE (RFC 7636) S256 challenge; the IdP only accepts the code paired with the verifier we hold |
| ID-token replay | OIDC `nonce` claim must match the cookie's nonce |
| Algorithm confusion (HS256 swap, `alg: none`) | Strict `algorithms=["RS256"]` allowlist enforced before signature check |
| Signing-key rotation | JWKS fetched + cached on demand; cache miss / unknown `kid` triggers a refresh |
| Multi-audience token confusion | OIDC `azp` must equal our `client_id` when `aud` is a list |
| Stale-cookie replay | Cookie carries `created_at`; 10-minute TTL; cleared on first callback (single-use) |
| Open-redirect via `return_to` | Only same-site relative paths accepted (must start with `/`, not `//`); falls back to `/admin/dashboard` |
| Token leak via URL | The issued JWT lives in the URL fragment (`#token=…`), not the query string — fragments don't appear in server logs or referer headers |
| Token leak via browser history | `history.replaceState` wipes the fragment immediately after the JS stores the token |
| Search-engine indexing | `<meta name="robots" content="noindex,nofollow">` on `/admin/login-complete` |
| Account takeover via auto-create | Auto-link refused on email collision (see § Auto-create) |
| Cookie shadowing on subdomains | `__Host-` cookie-name prefix on HTTPS forces `Secure + Path=/ + no Domain` |

## Adding another provider

Follow `GoogleOIDCProvider`'s pattern. Each provider needs:

* Hard-coded `AUTHORIZE_ENDPOINT`, `TOKEN_ENDPOINT`, `JWKS_URI`, `ISSUER` (or
  fetch from `.well-known/openid-configuration` at startup if the IdP doesn't
  pin its endpoints).
* `build_authorize_url()` — mostly standard OIDC, but providers may want extras.
* `exchange_code()` — Google uses form-encoded, GitHub uses JSON; the shape
  differs per provider, which is why each carries its own method.
* A matching `OIDCClaimMapper` subclass that translates the provider's claim
  names into the neutral `ExternalIdentityData` fields.

The router does not change — `build_oauth_router(providers)` iterates whatever
list you pass to `OAuthExtension`, so mounting GitHub alongside Google is just
adding another instance to the list.

## Disabling

Drop the `OAuthExtension` from `extensions=[…]`. The `/api/v1/oauth/*` routes
disappear, the login page's `/_login_contract` endpoint returns
`{"oauth_providers": []}`, and the OAuth buttons stop rendering. The
`external_identities` table stays in the database — delete it explicitly to
clean up.

## See also

* [Extensions](extensions.md) — the SPI this builds on.
* [Auth architecture](auth-architecture.md) — providers and the
  `OAuthCapableUserProvider` capability Protocol.
