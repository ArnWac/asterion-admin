"""SMTP email notifier extension.

Delivers the framework's password-reset and member-invite tokens over SMTP.
The extension does NOT auto-mount routes or touch the admin context — it just
exposes :class:`SmtpEmailNotifier`, which satisfies both
:class:`~asterion.auth.password_reset.PasswordResetNotifier` and
:class:`~asterion.auth.invite.InviteNotifier`. Wire one instance into both
``create_admin`` keywords::

    from asterion import create_admin
    from asterion.extensions.email import SmtpEmailNotifier

    mailer = SmtpEmailNotifier.from_env()
    app = create_admin(
        config=...,
        password_reset_notifier=mailer,
        invite_notifier=mailer,
    )

Subclass and override :meth:`SmtpEmailNotifier.render_reset` /
:meth:`SmtpEmailNotifier.render_invite` to brand the emails.

Dependencies
------------

Requires ``aiosmtplib``::

    pip install asterion-admin[email]

Importing this module without it is safe (the dependency loads lazily inside
the send path); only an actual SMTP send without it — and without a custom
``transport=`` — raises a clear :class:`ImportError`.
"""

from __future__ import annotations

from asterion.extensions.email.notifier import (
    EmailContent,
    EmailRenderer,
    SmtpEmailNotifier,
)

__all__ = ["EmailContent", "EmailRenderer", "SmtpEmailNotifier"]
