# OAuth / OIDC sign-in

The `auth_oauth` extension adds "Sign in with Google" (and, with new
provider classes, any other OIDC IdP) to the admin UI. The full flow:

```text
Browser                       Adminfoundry                          Google
  │  click "Sign in with Google"
  │  GET /api/v1/oauth/google/login
  │ ────────────────────────► │
  │                           │ generate state + nonce + PKCE
  │                           │ seal them into HttpOnly cookie
  │ ◄──────── 302 ─────────── │ Location: accounts.google.com/...
  │  follow redirect ───────────────────────────────────────────► │
  │                                                                │ user consents
  │ ◄────── 302 ─── /api/v1/oauth/google/callback?code=…&state=… ── │
  │  follow redirect          │
  │ ────────────────────────► │ unseal cookie, verify state
  │                           │ POST oauth2.googleapis.com/token
  │                           │      (with PKCE code_verifier)  ──► │
  │                           │ ◄────── id_token + access_token ─── │
  │                           │ fetch JWKS (cached), verify
  │                           │ map claims → ExternalIdentityData
  │                           │ find_or_create user
  │                           │ mint framework JWT
  │ ◄──── 302 ─── /admin/login-complete#token=<jwt>&return_to=… ─── │
  │  follow redirect          │
  │ ────────────────────────► │ static HTML + JS
  │  JS reads #token,         │
  │  stores in localStorage,  │
  │  redirects to return_to   │
```

End result: the user lands on `/admin/dashboard` (or the path they
were headed for before being bounced to login) with a valid framework
JWT in localStorage — the same key the password-login flow populates.

---

## Setup

### 1. Register the OAuth client at Google

In the Google Cloud console:

* **OAuth client type:** Web application
* **Authorized redirect URIs:** Your callback URL exactly:
  `https://your-app.example.com/api/v1/oauth/google/callback`
* For local dev: `http://localhost:8000/api/v1/oauth/google/callback`
  works only on `http://localhost` (Google permits it as a special
  case; HTTPS for any other host).

Copy the `client_id` and `client_secret` — the extension needs both.

### 2. Wire the extension into `create_admin`

```python
from asterion import create_admin, CoreAdminConfig
from asterion.extensions.auth_oauth import (
    OAuthExtension,
    GoogleOIDCProvider,
)

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
            # See § Auto-create below before flipping this on.
            auto_create_users=False,
        ),
    ],
)
```

### 3. Generate the ExternalIdentity migration

The framework ships no migration for the extension's table — see
[extensions.md § Migration story](extensions.md#migration-story). In
your project:

```bash
# Make sure migrations/shared/env.py imports the extension so
# autogenerate sees its tables. Add this line near the top:
#
#     import asterion.extensions.auth_oauth  # noqa: F401

alembic -c alembic_shared.ini revision --autogenerate -m "add external_identities"
alembic -c alembic_shared.ini upgrade head
```

### 4. (Optional) Wire a custom user provider

The default `BuiltinOAuthUserProvider` operates on the framework's
`User` table. If your app uses an external identity store (LDAP, an
existing user table, an IAM service), implement the
`OAuthCapableUserProvider` Protocol and pass it in:

```python
from asterion.extensions.auth_oauth import OAuthCapableUserProvider

class MyOAuthUserProvider:
    async def find_or_create_by_external_identity(self, **kwargs):
        ...

OAuthExtension(providers=[...], user_provider=MyOAuthUserProvider())
```

See [auth-architecture.md § Optional capabilities](auth-architecture.md#optional-capabilities-oauthcapableuserprovider).

---

## Auto-create

`auto_create_users` defaults to `False` — only pre-provisioned users
(those who already have an `ExternalIdentity` row pointing at them)
can sign in. This is the safe default for most deployments: an
operator explicitly invites people, who then sign in via OAuth and
inherit the existing account.

Setting `auto_create_users=True` lets the OAuth callback create a
fresh `User` + `ExternalIdentity` pair when an unknown subject signs
in. The `BuiltinOAuthUserProvider` then enforces three safety rules:

1. **`email_verified` must be `True`** in the IdP claims. Google +
   most major IdPs send it; "personal" providers that don't get
   refused.
2. **No silent linking by email.** If a `User` with the same email
   already exists (maybe from password signup), the OAuth flow
   refuses rather than auto-linking. Otherwise an attacker who can
   register a Google account with a victim's email could take over
   the victim's account. Account linking should be an explicit flow
   that authenticates both factors — which this version doesn't ship.
3. **Inactive users can't sign in.** A linked identity whose backing
   user is deactivated returns 401, matching the password-login
   behaviour.

Custom user providers can override any of these by implementing the
Protocol themselves with different rules.

---

## Security properties

What the extension protects against, and how:

| Threat | Defence |
|---|---|
| CSRF on the callback | Per-flow `state` value sealed into an HttpOnly cookie; mismatched state → 302 to `/admin/login?oauth_error=state_mismatch` |
| Authorization-code interception | PKCE (RFC 7636) S256 challenge; the IdP only accepts the code paired with the verifier we still hold |
| ID-token replay | OIDC `nonce` claim must match the cookie's nonce |
| Algorithm confusion (HS256 swap, `alg: none`) | Strict `algorithms=["RS256"]` allowlist enforced before signature check |
| Signing key rotation | JWKS fetched + cached on demand; cache miss / unknown `kid` triggers a refresh |
| Multi-audience token confusion | OIDC `azp` must equal our `client_id` when `aud` is a list |
| Stale-cookie replay | Cookie carries `created_at`; 10-minute TTL; cookie cleared on first callback (single-use) |
| Open-redirect via `return_to` | Only same-site relative paths accepted (must start with `/`, not `//`); falls back to `/admin/dashboard` |
| Token leak via URL | Issued JWT lives in URL fragment (`#token=…`), not query string — fragments don't appear in server logs or referer headers |
| Token leak via browser history | `history.replaceState` wipes the fragment immediately after the JS stores the token |
| Index by search engines | `<meta name="robots" content="noindex,nofollow">` on `/admin/login-complete` |
| Account takeover via auto-create | Auto-link refused on email collision (see § Auto-create) |
| Cookie shadowing on subdomains | `__Host-` cookie name prefix on HTTPS forces browser-enforced `Secure + Path=/ + no Domain` |

---

## Adding another provider

Subclass `GoogleOIDCProvider`'s pattern. Each provider needs:

* Hard-coded `AUTHORIZE_ENDPOINT`, `TOKEN_ENDPOINT`, `JWKS_URI`,
  `ISSUER` (or fetch from `.well-known/openid-configuration` at
  startup if the IdP doesn't pin its endpoints).
* `build_authorize_url()` — the params Google needs are mostly
  standard OIDC, but providers may want extras.
* `exchange_code()` — Google uses form-encoded; GitHub uses JSON; the
  shape differs per provider, which is why each carries its own
  method rather than inheriting from a generic one.
* A matching `OIDCClaimMapper` subclass that translates the
  provider's claim names into the neutral `ExternalIdentityData`
  fields.

The router doesn't need to change — `build_oauth_router(providers)`
iterates whatever list of providers you pass to `OAuthExtension`, so
mounting GitHub alongside Google is just adding another instance to
the list.

---

## Disabling

Drop the `OAuthExtension` from `extensions=[…]`. The `/api/v1/oauth/*`
routes disappear; the login page's `/_login_contract` endpoint
returns `{"oauth_providers": []}` and the OAuth buttons stop
rendering. The `external_identities` table stays in the database —
delete it explicitly if you want to clean it up.
