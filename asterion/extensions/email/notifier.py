"""SMTP notifier — delivers password-reset + member-invite emails.

One class satisfies BOTH framework notifier Protocols
(:class:`~asterion.auth.password_reset.PasswordResetNotifier` via
``send_reset`` and :class:`~asterion.auth.invite.InviteNotifier` via
``send_invite``), so a host app wires a single instance into both
``create_admin`` keywords::

    from asterion import create_admin
    from asterion.extensions.email import SmtpEmailNotifier

    mailer = SmtpEmailNotifier.from_env()   # reads ASTERION_SMTP_*
    app = create_admin(
        config=...,
        password_reset_notifier=mailer,
        invite_notifier=mailer,
    )

Templates are app-customisable
------------------------------

The message bodies are produced by :meth:`render_reset` /
:meth:`render_invite`, which return an :class:`EmailContent`
(subject + plaintext + optional HTML). Override them in a subclass to
brand the emails — that's the supported extension point::

    class MyMailer(SmtpEmailNotifier):
        def render_invite(self, *, email, token, tenant_slug=None, request=None):
            link = self.invite_link(token)
            return EmailContent(
                subject=f"Welcome to {tenant_slug}",
                text=f"Set your password: {link}",
                html=my_html_template(link, tenant_slug),
            )

Dependency + transport
----------------------

The SMTP send uses ``aiosmtplib`` (optional extra ``asterion-admin[email]``),
imported lazily inside :meth:`_default_send` so importing this module without
it installed is fine. For tests — and for routing through an app's own mail
pipeline — pass a ``transport`` callable; it receives the built
:class:`email.message.EmailMessage` and is responsible for delivery, so no
real SMTP server (or ``aiosmtplib``) is needed.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

Transport = Callable[[EmailMessage], Awaitable[None]]


@dataclass
class EmailContent:
    """Rendered email body. ``html`` is optional; when set it's attached as
    an ``text/html`` alternative alongside the plaintext part."""

    subject: str
    text: str
    html: str | None = None


#: An app-supplied renderer for a custom email event. Receives the recipient
#: address and a free-form context dict; returns the rendered email body.
EmailRenderer = Callable[[str, dict[str, Any]], EmailContent]


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SmtpEmailNotifier:
    """SMTP-backed reset + invite notifier with overridable templates."""

    def __init__(
        self,
        *,
        host: str,
        from_addr: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        start_tls: bool = True,
        timeout: float = 10.0,
        app_name: str = "Admin",
        reset_url: str | None = None,
        invite_url: str | None = None,
        templates: dict[str, EmailRenderer] | None = None,
        transport: Transport | None = None,
    ) -> None:
        """Construct the notifier.

        ``reset_url`` / ``invite_url`` are link templates containing a
        ``{token}`` placeholder (e.g. ``https://app.example.com/reset?token={token}``).
        When unset, the default templates fall back to printing the raw token
        with instructions — override :meth:`render_reset` / :meth:`render_invite`
        for anything richer.

        ``use_tls`` (implicit TLS, usually port 465) and ``start_tls``
        (STARTTLS upgrade, usually port 587) are mutually exclusive; set the
        one your server expects.
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.start_tls = start_tls
        self.timeout = timeout
        self.from_addr = from_addr
        self.app_name = app_name
        self.reset_url = reset_url
        self.invite_url = invite_url
        self._templates: dict[str, EmailRenderer] = dict(templates or {})
        self._transport = transport

    # -- construction from env --------------------------------------------

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> SmtpEmailNotifier:
        """Build from ``ASTERION_SMTP_*`` environment variables.

        Required: ``ASTERION_SMTP_HOST``, ``ASTERION_SMTP_FROM``. Optional:
        ``ASTERION_SMTP_PORT`` (587), ``ASTERION_SMTP_USERNAME``,
        ``ASTERION_SMTP_PASSWORD``, ``ASTERION_SMTP_USE_TLS`` (false),
        ``ASTERION_SMTP_START_TLS`` (true), ``ASTERION_SMTP_APP_NAME`` (Admin),
        ``ASTERION_RESET_URL``, ``ASTERION_INVITE_URL``.
        """
        host = _env("ASTERION_SMTP_HOST")
        from_addr = _env("ASTERION_SMTP_FROM")
        if not host or not from_addr:
            raise ValueError(
                "SmtpEmailNotifier.from_env() requires ASTERION_SMTP_HOST and "
                "ASTERION_SMTP_FROM to be set."
            )
        return cls(
            host=host,
            from_addr=from_addr,
            port=int(_env("ASTERION_SMTP_PORT", "587")),  # type: ignore[arg-type]
            username=_env("ASTERION_SMTP_USERNAME"),
            password=_env("ASTERION_SMTP_PASSWORD"),
            use_tls=_env_bool("ASTERION_SMTP_USE_TLS", False),
            start_tls=_env_bool("ASTERION_SMTP_START_TLS", True),
            app_name=_env("ASTERION_SMTP_APP_NAME", "Admin"),  # type: ignore[arg-type]
            reset_url=_env("ASTERION_RESET_URL"),
            invite_url=_env("ASTERION_INVITE_URL"),
            transport=transport,
        )

    # -- link helpers ------------------------------------------------------

    def reset_link(self, token: str) -> str:
        return self.reset_url.format(token=token) if self.reset_url else token

    def invite_link(self, token: str) -> str:
        return self.invite_url.format(token=token) if self.invite_url else token

    # -- templates (override these for custom branding) --------------------

    def render_reset(
        self,
        *,
        email: str,
        token: str,
        request: Request | None = None,
    ) -> EmailContent:
        link = self.reset_link(token)
        intro = f"We received a request to reset the password for {email}."
        cta = (
            f"Open this link to choose a new password:\n\n{link}"
            if self.reset_url
            else f"Use this reset token to choose a new password:\n\n{link}"
        )
        return EmailContent(
            subject=f"{self.app_name}: reset your password",
            text=f"{intro}\n\n{cta}\n\nIf you didn't request this, ignore this email.",
        )

    def render_invite(
        self,
        *,
        email: str,
        token: str,
        tenant_slug: str | None = None,
        request: Request | None = None,
    ) -> EmailContent:
        link = self.invite_link(token)
        where = f" to {tenant_slug}" if tenant_slug else ""
        cta = (
            f"Open this link to set your password and get started:\n\n{link}"
            if self.invite_url
            else f"Use this invite token to set your password:\n\n{link}"
        )
        return EmailContent(
            subject=f"{self.app_name}: you've been invited{where}",
            text=f"You've been invited{where}.\n\n{cta}",
        )

    # -- SPI methods -------------------------------------------------------

    async def send_reset(
        self,
        *,
        email: str,
        token: str,
        request: Request | None = None,
    ) -> None:
        content = self.render_reset(email=email, token=token, request=request)
        await self._deliver(email, content)

    async def send_invite(
        self,
        *,
        email: str,
        token: str,
        tenant_slug: str | None = None,
        request: Request | None = None,
    ) -> None:
        content = self.render_invite(
            email=email, token=token, tenant_slug=tenant_slug, request=request
        )
        await self._deliver(email, content)

    # -- generic app events ------------------------------------------------

    def register_template(self, event: str, renderer: EmailRenderer) -> None:
        """Register an app-defined email event.

        ``renderer`` is called as ``renderer(to, context)`` and returns an
        :class:`EmailContent`. Lets the host app send arbitrary emails
        (welcome, receipt, "export ready", …) through the same SMTP transport
        + delivery path as reset/invite, without subclassing::

            mailer.register_template(
                "welcome",
                lambda to, ctx: EmailContent(
                    subject="Welcome!",
                    text=f"Hi {ctx.get('name', to)}, glad you're here.",
                ),
            )
            await mailer.send("welcome", "newuser@example.com", context={"name": "Sam"})

        Re-registering the same ``event`` replaces the previous renderer.
        """
        self._templates[event] = renderer

    def render_event(
        self,
        *,
        event: str,
        to: str,
        context: dict[str, Any],
        request: Request | None = None,
    ) -> EmailContent:
        """Render a registered app event into an :class:`EmailContent`.

        Looks up the renderer registered via :meth:`register_template`.
        Subclasses may override this to dispatch events however they like
        (e.g. a template engine keyed by ``event``).
        """
        renderer = self._templates.get(event)
        if renderer is None:
            raise KeyError(
                f"No email template registered for event {event!r}. "
                f"Register one via register_template({event!r}, ...) or override "
                "render_event() in a subclass."
            )
        return renderer(to, context)

    async def send(
        self,
        event: str,
        to: str,
        *,
        context: dict[str, Any] | None = None,
        request: Request | None = None,
    ) -> None:
        """Render ``event`` and deliver it to ``to``.

        The app-facing entry point for custom email events; reset/invite keep
        their dedicated ``send_reset`` / ``send_invite`` (they're framework SPI
        methods). All three share the same build + transport path.
        """
        content = self.render_event(event=event, to=to, context=context or {}, request=request)
        await self._deliver(to, content)

    # -- delivery ----------------------------------------------------------

    def build_message(self, to: str, content: EmailContent) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = content.subject
        msg.set_content(content.text)
        if content.html is not None:
            msg.add_alternative(content.html, subtype="html")
        return msg

    async def _deliver(self, to: str, content: EmailContent) -> None:
        message = self.build_message(to, content)
        if self._transport is not None:
            await self._transport(message)
        else:
            await self._default_send(message)

    async def _default_send(self, message: EmailMessage) -> None:
        try:
            import aiosmtplib
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "SmtpEmailNotifier needs aiosmtplib. Install the extra:\n"
                "    pip install asterion-admin[email]\n"
                "or pass a custom transport= to route through your own mailer."
            ) from exc

        await aiosmtplib.send(
            message,
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            use_tls=self.use_tls,
            start_tls=self.start_tls,
            timeout=self.timeout,
        )
