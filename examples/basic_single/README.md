# basic_single — single-tenant blog example

A minimal single-tenant adminfoundry app: blog posts managed by a global superadmin.

## Run

```bash
uvicorn examples.basic_single.app:app --reload
```

Then visit http://127.0.0.1:8000/admin

Demo credentials are printed to the console on startup. The SQLite database
lives in `basic_single.db`.

## What's registered

- `PostAdmin` — a guided tour of the **full `ModelAdmin` surface**: list
  badges, column filters, date-hierarchy drill-down, inline edit, fieldsets
  rendered as **tabs**, placeholders + textarea widgets, conditional +
  dependent fields, a protected field (`api_secret`), a per-field policy
  (`internal_notes` read-only for non-superadmins), calculated fields
  (`word_count`, `read_time`), a bulk-delete action, and an inline
  `Comment` child. Read `admin_config.py` top-to-bottom as a feature index.

The tenant RBAC builtins (`TenantRoleAdmin`, etc.) are skipped because this
example sets `enable_multi_tenant=False` and `enable_builtin_admins=False`.

## Seeding

`seed.py` creates one global superadmin (`admin@example.com` / `admin123`)
on every startup. It is idempotent — re-running does not overwrite or
duplicate the user.

You can also seed without booting the server:

```bash
python -m examples.basic_single.seed
```

## Optional: Sign in with Google

The example wires the `auth_oauth` extension when two environment
variables are set. With them present, the login page grows a
"Sign in with Google" button alongside the password form.

### 1. Register the OAuth client at Google

In the Google Cloud Console (https://console.cloud.google.com/apis/credentials):

- Create an **OAuth 2.0 Client ID** of type **Web application**
- Under **Authorized redirect URIs**, add:
  - `http://localhost:8000/api/v1/oauth/google/callback`

Copy the **Client ID** and **Client secret**.

### 2. Run with the env vars set

```bash
export GOOGLE_OAUTH_CLIENT_ID="...apps.googleusercontent.com"
export GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-..."
uvicorn examples.basic_single.app:app --reload
```

On Windows PowerShell:

```powershell
$env:GOOGLE_OAUTH_CLIENT_ID = "...apps.googleusercontent.com"
$env:GOOGLE_OAUTH_CLIENT_SECRET = "GOCSPX-..."
uvicorn examples.basic_single.app:app --reload
```

### 3. Sign in

Visit http://127.0.0.1:8000/admin/login and click **Google**. The
example is configured with `auto_create_users=True` so any Google user
with a verified email gets an auto-created admin account on first
sign-in. (For production, set this to `False` and pre-provision users
via the admin UI — see [docs/auth-oauth.md](../../docs/auth-oauth.md)
for the full security defaults.)

### How to turn it off

Just don't set the env vars. The example skips wiring the
`OAuthExtension` when either is missing, and the login page falls
back to the password-only form.
