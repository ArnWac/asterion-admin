# Email

asterion's core owns the **token lifecycle** for password resets and member
invites but not delivery — sending is app-specific. The bundled
`asterion.extensions.email` extension provides ready-made delivery over SMTP or
a transactional-email provider, overridable templates, and an optional
transactional outbox. Core ships only the notifier Protocols + dev-only logging
defaults, so nothing here is pulled in unless you opt in.

## Install

```bash
pip install asterion-admin[email]          # SMTP (aiosmtplib) + Jinja templates
pip install asterion-admin[email-resend]   # Resend HTTP API (httpx)
pip install asterion-admin[email-ses]      # Amazon SES (boto3)
```

## Wire it up

One notifier instance satisfies **both** `PasswordResetNotifier` and
`InviteNotifier`, so it goes into both `create_admin` keywords:

```python
from asterion import create_admin
from asterion.extensions.email import SmtpEmailNotifier

mailer = SmtpEmailNotifier.from_env()
app = create_admin(
    config=...,
    password_reset_notifier=mailer,
    invite_notifier=mailer,
)
```

### SMTP env vars (`from_env`)

| Variable | Default | Notes |
|---|---|---|
| `ASTERION_SMTP_HOST` | — | **required** |
| `ASTERION_SMTP_FROM` | — | **required**, From address |
| `ASTERION_SMTP_PORT` | `587` | |
| `ASTERION_SMTP_USERNAME` / `_PASSWORD` | — | auth |
| `ASTERION_SMTP_START_TLS` | `true` | STARTTLS (port 587) |
| `ASTERION_SMTP_USE_TLS` | `false` | implicit TLS (port 465) |
| `ASTERION_SMTP_APP_NAME` | `Admin` | shown in subjects |
| `ASTERION_INVITE_URL` / `ASTERION_RESET_URL` | — | link template with `{token}` |
| `ASTERION_EMAIL_TEMPLATE_DIR` | — | your template overrides |

Set `ASTERION_INVITE_URL` / `ASTERION_RESET_URL` to your app's accept/reset
pages, e.g. `https://app.example.com/accept?token={token}`, so the emails carry
a real link instead of the raw token.

## Providers (alternatives to SMTP)

Same API, different transport — swap the class:

```python
from asterion.extensions.email import ResendEmailNotifier, SesEmailNotifier

mailer = ResendEmailNotifier.from_env()   # RESEND_API_KEY + ASTERION_EMAIL_FROM
# or
mailer = SesEmailNotifier.from_env()      # AWS creds via the standard chain
```

Both render identically (templates below) and only differ in how the message is
sent (Resend → JSON HTTP API via httpx; SES → boto3 SESv2). For tests or to
route through your own pipeline, pass `send=` (Resend/SES) or `transport=`
(SMTP) — a callable that receives the rendered payload/message.

## Templates

Bodies render from Jinja templates named `<name>.subject.txt`, `<name>.txt`,
and (optional) `<name>.html`. They resolve from your `template_dir` first, then
asterion's packaged defaults — so dropping a file with the same name overrides
just that one email:

```
my_templates/
  invite.subject.txt
  invite.txt
  invite.html      # optional; adds an HTML alternative
```

```python
mailer = SmtpEmailNotifier.from_env()  # ASTERION_EMAIL_TEMPLATE_DIR=./my_templates
```

Template context: `app_name`, `email`, `token`, `link`, `has_url` (reset);
plus `tenant_slug` (invite). For full programmatic control, subclass and
override `render_reset` / `render_invite` instead. Without `jinja2` installed
the notifier falls back to built-in plaintext.

## Custom app events

Beyond reset + invite, send your own emails (welcome, receipt, "export ready",
…) through the same transport — register a renderer or drop a `<event>`
template:

```python
from asterion.extensions.email import EmailContent

mailer.register_template(
    "welcome",
    lambda to, ctx: EmailContent(subject="Welcome!", text=f"Hi {ctx['name']}"),
)
await mailer.send("welcome", "newuser@example.com", context={"name": "Sam"})
```

`send(event, to, context=...)` resolves a registered renderer first, then a
`<event>` Jinja template, else raises `KeyError`.

## Robust delivery: the transactional outbox

Sending inline couples the request to the mail server (a slow SMTP call slows
the invite response; a transient failure loses the email). Wrap any notifier in
`OutboxEmailNotifier` to **persist** the email in the *same* DB transaction as
the invite/user that triggered it, then send it from a background worker:

```python
from asterion.extensions.email import OutboxEmailNotifier, SmtpEmailNotifier

real = SmtpEmailNotifier.from_env()
queued = OutboxEmailNotifier(real)          # enqueues in request.state.db_session
app = create_admin(config=..., invite_notifier=queued, password_reset_notifier=queued)
```

A worker drains the queue with bounded retries + backoff:

```python
from asterion.extensions.email import process_outbox

async def run_worker(db, real_notifier):
    async with db.session() as s:
        async with s.begin():
            await process_outbox(s, real_notifier, batch=50, max_attempts=5)
```

Run it from your own cron/loop (e.g. every minute).

### Migration

Following asterion's extension convention (same as `auth_oauth`), the framework
ships **no** migration for the `email_outbox` table. Import the model so it
registers on the global metadata, then autogenerate against your env.py:

```python
from asterion.extensions.email import EmailOutbox  # noqa: F401  (registers the table)
```

```bash
alembic revision --autogenerate -m "add email_outbox"
alembic upgrade head
```

Outside a request (e.g. a job that sends mail directly), give the outbox a
session factory: `OutboxEmailNotifier(real, session_factory=runtime.db.session)`.
