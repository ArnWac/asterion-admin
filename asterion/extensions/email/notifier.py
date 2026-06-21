"""Email notifier base + SMTP transport.

:class:`BaseEmailNotifier` owns everything transport-agnostic — rendering
(reset / invite / custom app events), the framework SPI methods
(``send_reset`` / ``send_invite`` / ``send``), and template resolution — and
delegates the actual send to an abstract :meth:`BaseEmailNotifier.deliver`.
Concrete notifiers (SMTP here; Resend / SES in
:mod:`asterion.extensions.email.providers`; the
:class:`~asterion.extensions.email.outbox.OutboxEmailNotifier`) only implement
delivery.

One notifier satisfies BOTH framework Protocols
(:class:`~asterion.auth.password_reset.PasswordResetNotifier` via ``send_reset``
and :class:`~asterion.auth.invite.InviteNotifier` via ``send_invite``), so a
host app wires a single instance into both ``create_admin`` keywords::

    from asterion import create_admin
    from asterion.extensions.email import SmtpEmailNotifier

    mailer = SmtpEmailNotifier.from_env()
    app = create_admin(
        config=...,
        password_reset_notifier=mailer,
        invite_notifier=mailer,
    )

Templates
---------

Bodies are produced by :meth:`render_reset` / :meth:`render_invite` /
:meth:`render_event`. Two override paths, in order of resolution:

1. **Jinja templates** — ``<name>.subject.txt`` / ``<name>.txt`` /
   ``<name>.html`` resolved first from an app-supplied ``template_dir`` then
   from the packaged defaults (``asterion/extensions/email/templates``). Drop a
   file with the same name in your ``template_dir`` to override one email.
   Needs ``jinja2`` (ships in the ``email`` extra); without it the notifier
   falls back to the built-in plaintext strings.
2. **Python override** — subclass and override ``render_*`` for full control,
   or ``register_template(event, renderer)`` for a custom event.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

Transport = Callable[[EmailMessage], Awaitable[None]]

#: Where the bundled default Jinja templates live.
_PACKAGED_TEMPLATES = Path(__file__).parent / "templates"


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


class BaseEmailNotifier:
    """Transport-agnostic rendering + SPI. Subclasses implement :meth:`deliver`."""

    def __init__(
        self,
        *,
        from_addr: str,
        app_name: str = "Admin",
        reset_url: str | None = None,
        invite_url: str | None = None,
        templates: dict[str, EmailRenderer] | None = None,
        template_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.from_addr = from_addr
        self.app_name = app_name
        self.reset_url = reset_url
        self.invite_url = invite_url
        self.template_dir = template_dir
        self._templates: dict[str, EmailRenderer] = dict(templates or {})
        self._jinja_env: Any = None
        self._jinja_ready = False

    # -- link helpers ------------------------------------------------------

    def reset_link(self, token: str) -> str:
        return self.reset_url.format(token=token) if self.reset_url else token

    def invite_link(self, token: str) -> str:
        return self.invite_url.format(token=token) if self.invite_url else token

    # -- Jinja resolution --------------------------------------------------

    def _jinja(self) -> Any:
        """Lazily build a Jinja Environment (app ``template_dir`` overriding
        the packaged defaults). Returns ``None`` when ``jinja2`` isn't
        installed — callers then fall back to the string templates."""
        if self._jinja_ready:
            return self._jinja_env
        self._jinja_ready = True
        try:
            from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape
        except ImportError:
            self._jinja_env = None
            return None
        search: list[Any] = []
        if self.template_dir is not None:
            search.append(FileSystemLoader(str(self.template_dir)))
        search.append(FileSystemLoader(str(_PACKAGED_TEMPLATES)))
        self._jinja_env = Environment(
            loader=ChoiceLoader(search),
            autoescape=select_autoescape(["html"]),
        )
        return self._jinja_env

    def _render_template(self, name: str, context: dict[str, Any]) -> EmailContent | None:
        """Render ``<name>.subject.txt`` + ``<name>.txt`` (+ optional
        ``<name>.html``). Returns ``None`` when Jinja or the required
        templates are absent, so the caller can fall back."""
        env = self._jinja()
        if env is None:
            return None
        from jinja2 import TemplateNotFound

        ctx = {"app_name": self.app_name, **context}
        try:
            subject = env.get_template(f"{name}.subject.txt").render(ctx).strip()
            text = env.get_template(f"{name}.txt").render(ctx)
        except TemplateNotFound:
            return None
        try:
            html: str | None = env.get_template(f"{name}.html").render(ctx)
        except TemplateNotFound:
            html = None
        return EmailContent(subject=subject, text=text, html=html)

    # -- templates (override these, or drop Jinja files, for branding) -----

    def render_reset(
        self,
        *,
        email: str,
        token: str,
        request: Request | None = None,
    ) -> EmailContent:
        link = self.reset_link(token)
        rendered = self._render_template(
            "reset", {"email": email, "token": token, "link": link, "has_url": bool(self.reset_url)}
        )
        if rendered is not None:
            return rendered
        # Plaintext fallback (no jinja2 / no template).
        cta = (
            f"Open this link to choose a new password:\n\n{link}"
            if self.reset_url
            else f"Use this reset token to choose a new password:\n\n{link}"
        )
        return EmailContent(
            subject=f"{self.app_name}: reset your password",
            text=(
                f"We received a request to reset the password for {email}.\n\n"
                f"{cta}\n\nIf you didn't request this, ignore this email."
            ),
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
        rendered = self._render_template(
            "invite",
            {
                "email": email,
                "token": token,
                "link": link,
                "tenant_slug": tenant_slug,
                "has_url": bool(self.invite_url),
            },
        )
        if rendered is not None:
            return rendered
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

    # -- generic app events ------------------------------------------------

    def register_template(self, event: str, renderer: EmailRenderer) -> None:
        """Register an app-defined email event.

        ``renderer`` is called as ``renderer(to, context)`` and returns an
        :class:`EmailContent`. Lets the host app send arbitrary emails
        (welcome, receipt, …) through the same delivery path as reset/invite::

            mailer.register_template(
                "welcome",
                lambda to, ctx: EmailContent(
                    subject="Welcome!",
                    text=f"Hi {ctx.get('name', to)}, glad you're here.",
                ),
            )
            await mailer.send("welcome", "newuser@example.com", context={"name": "Sam"})

        Re-registering the same ``event`` replaces the previous renderer. As an
        alternative, drop ``<event>.subject.txt`` / ``<event>.txt`` templates
        into your ``template_dir`` — :meth:`send` falls back to those.
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
        """Render an app event: a registered renderer wins, else a
        ``<event>`` Jinja template, else :class:`KeyError`. Subclasses may
        override to dispatch however they like."""
        renderer = self._templates.get(event)
        if renderer is not None:
            return renderer(to, context)
        rendered = self._render_template(event, {"to": to, **context})
        if rendered is not None:
            return rendered
        raise KeyError(
            f"No email template registered for event {event!r}. Register one via "
            f"register_template({event!r}, ...), drop a {event!r} template into your "
            "template_dir, or override render_event() in a subclass."
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
        await self.deliver(to=email, content=content, request=request)

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
        await self.deliver(to=email, content=content, request=request)

    async def send(
        self,
        event: str,
        to: str,
        *,
        context: dict[str, Any] | None = None,
        request: Request | None = None,
    ) -> None:
        """Render ``event`` and deliver it. The app-facing entry point for
        custom email events; reset/invite keep their dedicated SPI methods but
        share this delivery path."""
        content = self.render_event(event=event, to=to, context=context or {}, request=request)
        await self.deliver(to=to, content=content, request=request)

    # -- delivery (implemented by subclasses) ------------------------------

    async def deliver(
        self,
        *,
        to: str,
        content: EmailContent,
        request: Request | None = None,
    ) -> None:
        raise NotImplementedError


class SmtpEmailNotifier(BaseEmailNotifier):
    """SMTP-backed notifier (via ``aiosmtplib``)."""

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
        template_dir: str | os.PathLike[str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        """``use_tls`` (implicit TLS, usually port 465) and ``start_tls``
        (STARTTLS upgrade, usually port 587) are mutually exclusive — set the
        one your server expects. Pass ``transport`` (a callable receiving the
        built :class:`email.message.EmailMessage`) to route through your own
        pipeline or to test without a real SMTP server."""
        super().__init__(
            from_addr=from_addr,
            app_name=app_name,
            reset_url=reset_url,
            invite_url=invite_url,
            templates=templates,
            template_dir=template_dir,
        )
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.start_tls = start_tls
        self.timeout = timeout
        self._transport = transport

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> SmtpEmailNotifier:
        """Build from ``ASTERION_SMTP_*`` environment variables.

        Required: ``ASTERION_SMTP_HOST``, ``ASTERION_SMTP_FROM``. Optional:
        ``ASTERION_SMTP_PORT`` (587), ``ASTERION_SMTP_USERNAME``,
        ``ASTERION_SMTP_PASSWORD``, ``ASTERION_SMTP_USE_TLS`` (false),
        ``ASTERION_SMTP_START_TLS`` (true), ``ASTERION_SMTP_APP_NAME`` (Admin),
        ``ASTERION_EMAIL_TEMPLATE_DIR``, ``ASTERION_RESET_URL``,
        ``ASTERION_INVITE_URL``.
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
            template_dir=_env("ASTERION_EMAIL_TEMPLATE_DIR"),
            transport=transport,
        )

    def build_message(self, to: str, content: EmailContent) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = content.subject
        msg.set_content(content.text)
        if content.html is not None:
            msg.add_alternative(content.html, subtype="html")
        return msg

    async def deliver(
        self,
        *,
        to: str,
        content: EmailContent,
        request: Request | None = None,
    ) -> None:
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
