"""Email notifier extension.

Delivers the framework's password-reset and member-invite tokens — and any
app-defined email event — over SMTP or a transactional-email provider. The
extension does NOT auto-mount routes or touch the admin context; it exposes
notifiers that satisfy both
:class:`~asterion.auth.password_reset.PasswordResetNotifier` and
:class:`~asterion.auth.invite.InviteNotifier`, so one instance wires into both
``create_admin`` keywords::

    from asterion import create_admin
    from asterion.extensions.email import SmtpEmailNotifier

    mailer = SmtpEmailNotifier.from_env()
    app = create_admin(
        config=...,
        password_reset_notifier=mailer,
        invite_notifier=mailer,
    )

Transports
----------

* :class:`SmtpEmailNotifier` — SMTP via ``aiosmtplib`` (``[email]`` extra).
* :class:`ResendEmailNotifier` — Resend HTTP API via ``httpx``
  (``[email-resend]`` extra).
* :class:`SesEmailNotifier` — Amazon SES via ``boto3`` (``[email-ses]`` extra).

Templates (``[email]`` extra pulls ``jinja2``)
----------------------------------------------

Bodies render from ``<name>.subject.txt`` / ``<name>.txt`` / ``<name>.html``,
resolved from an app ``template_dir`` first then the packaged defaults. Without
``jinja2`` the notifiers fall back to built-in plaintext. Subclass and override
``render_*`` (or ``register_template``) for full control.

Robust delivery
---------------

Wrap any notifier in :class:`OutboxEmailNotifier` to enqueue emails into an
``email_outbox`` table (in the triggering request's transaction) and send them
later via :func:`process_outbox` from your own worker. The framework ships no
migration for that table — autogenerate it against your env.py (see
:mod:`asterion.extensions.email.outbox`).

All dependencies load lazily, so importing this package without the extras is
safe; only an actual send (without an injected client/transport) raises a clear
:class:`ImportError`.
"""

from __future__ import annotations

from asterion.extensions.email.notifier import (
    BaseEmailNotifier,
    EmailContent,
    EmailRenderer,
    SmtpEmailNotifier,
)
from asterion.extensions.email.outbox import (
    EmailOutbox,
    OutboxEmailNotifier,
    enqueue_email,
    process_outbox,
)
from asterion.extensions.email.providers import ResendEmailNotifier, SesEmailNotifier

__all__ = [
    "BaseEmailNotifier",
    "EmailContent",
    "EmailOutbox",
    "EmailRenderer",
    "OutboxEmailNotifier",
    "ResendEmailNotifier",
    "SesEmailNotifier",
    "SmtpEmailNotifier",
    "enqueue_email",
    "process_outbox",
]
