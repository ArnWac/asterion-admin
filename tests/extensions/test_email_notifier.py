"""SMTP email notifier extension.

The notifier satisfies both framework notifier Protocols and renders
overridable templates. Delivery is exercised through an injected transport,
so these tests need neither a real SMTP server nor ``aiosmtplib``.
"""

from __future__ import annotations

import sys
from email.message import EmailMessage

import pytest

from asterion.auth.invite import InviteNotifier
from asterion.auth.password_reset import PasswordResetNotifier
from asterion.extensions.email import EmailContent, SmtpEmailNotifier


class _Capture:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def __call__(self, message: EmailMessage) -> None:
        self.messages.append(message)


def _text(msg: EmailMessage) -> str:
    """Plaintext part of a (possibly multipart text+html) message."""
    body = msg.get_body(preferencelist=("plain",))
    return body.get_content() if body is not None else msg.get_content()


def _mailer(**overrides) -> tuple[SmtpEmailNotifier, _Capture]:
    cap = _Capture()
    kwargs = dict(
        host="smtp.example.com",
        from_addr="admin@example.com",
        reset_url="https://app.example.com/reset?token={token}",
        invite_url="https://app.example.com/accept?token={token}",
        app_name="Acme Admin",
        transport=cap,
    )
    kwargs.update(overrides)
    return SmtpEmailNotifier(**kwargs), cap


# --- Protocol conformance ---


def test_satisfies_both_notifier_protocols():
    mailer, _ = _mailer()
    assert isinstance(mailer, PasswordResetNotifier)
    assert isinstance(mailer, InviteNotifier)


# --- send_reset ---


@pytest.mark.asyncio
async def test_send_reset_builds_message_with_link():
    mailer, cap = _mailer()
    await mailer.send_reset(email="bob@example.com", token="tok-123")

    assert len(cap.messages) == 1
    msg = cap.messages[0]
    assert msg["To"] == "bob@example.com"
    assert msg["From"] == "admin@example.com"
    assert "Acme Admin" in msg["Subject"]
    body = _text(msg)
    assert "https://app.example.com/reset?token=tok-123" in body


@pytest.mark.asyncio
async def test_send_reset_without_url_uses_raw_token():
    mailer, cap = _mailer(reset_url=None)
    await mailer.send_reset(email="bob@example.com", token="raw-tok")
    assert "raw-tok" in _text(cap.messages[0])


# --- send_invite ---


@pytest.mark.asyncio
async def test_send_invite_includes_tenant_and_link():
    mailer, cap = _mailer()
    await mailer.send_invite(email="dave@example.com", token="inv-9", tenant_slug="acme")

    msg = cap.messages[0]
    assert msg["To"] == "dave@example.com"
    body = _text(msg)
    assert "acme" in body
    assert "https://app.example.com/accept?token=inv-9" in body


# --- template override ---


@pytest.mark.asyncio
async def test_subclass_can_override_templates():
    class Branded(SmtpEmailNotifier):
        def render_invite(self, *, email, token, tenant_slug=None, request=None):
            return EmailContent(
                subject="Custom subject",
                text=f"plain {self.invite_link(token)}",
                html=f"<a href='{self.invite_link(token)}'>join</a>",
            )

    cap = _Capture()
    mailer = Branded(
        host="h",
        from_addr="a@b.c",
        invite_url="https://x/accept?token={token}",
        transport=cap,
    )
    await mailer.send_invite(email="e@x.com", token="T")

    msg = cap.messages[0]
    assert msg["Subject"] == "Custom subject"
    # HTML alternative attached alongside the plaintext part.
    assert msg.is_multipart()
    types = {part.get_content_type() for part in msg.iter_parts()}
    assert {"text/plain", "text/html"} <= types
    html_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
    assert "https://x/accept?token=T" in html_part.get_content()


# --- generic app events ---


@pytest.mark.asyncio
async def test_register_and_send_custom_event():
    mailer, cap = _mailer()
    mailer.register_template(
        "welcome",
        lambda to, ctx: EmailContent(
            subject="Welcome!",
            text=f"Hi {ctx.get('name', to)}, glad you're here.",
        ),
    )
    await mailer.send("welcome", "newuser@example.com", context={"name": "Sam"})

    msg = cap.messages[0]
    assert msg["To"] == "newuser@example.com"
    assert msg["Subject"] == "Welcome!"
    assert "Hi Sam" in msg.get_content()


@pytest.mark.asyncio
async def test_send_unknown_event_raises():
    mailer, _ = _mailer()
    with pytest.raises(KeyError, match="welcome"):
        await mailer.send("welcome", "x@example.com")


def test_templates_can_be_passed_to_constructor():
    mailer, _ = _mailer(
        templates={"ping": lambda to, ctx: EmailContent(subject="Ping", text="pong")}
    )
    content = mailer.render_event(event="ping", to="a@b.c", context={})
    assert content.subject == "Ping"


@pytest.mark.asyncio
async def test_subclass_can_override_render_event():
    class Dispatcher(SmtpEmailNotifier):
        def render_event(self, *, event, to, context, request=None):
            return EmailContent(subject=f"evt:{event}", text=f"to={to}")

    cap = _Capture()
    mailer = Dispatcher(host="h", from_addr="a@b.c", transport=cap)
    await mailer.send("anything", "e@x.com", context={"k": "v"})
    assert cap.messages[0]["Subject"] == "evt:anything"


# --- Jinja templates (#3) ---


@pytest.mark.asyncio
async def test_default_templates_render_html_alternative():
    """With jinja2 available, the packaged reset template adds an HTML part."""
    mailer, cap = _mailer()
    await mailer.send_reset(email="bob@example.com", token="tok-7")
    msg = cap.messages[0]
    assert msg.is_multipart()
    html = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
    assert "tok-7" in html.get_content()


@pytest.mark.asyncio
async def test_template_dir_override_wins(tmp_path):
    (tmp_path / "reset.subject.txt").write_text("Override subject", encoding="utf-8")
    (tmp_path / "reset.txt").write_text("custom body {{ link }}", encoding="utf-8")
    mailer, cap = _mailer(template_dir=str(tmp_path))
    await mailer.send_reset(email="bob@example.com", token="zz")
    msg = cap.messages[0]
    assert msg["Subject"] == "Override subject"
    assert "custom body https://app.example.com/reset?token=zz" in _text(msg)


@pytest.mark.asyncio
async def test_custom_event_via_template_file(tmp_path):
    (tmp_path / "welcome.subject.txt").write_text("Hi {{ to }}", encoding="utf-8")
    (tmp_path / "welcome.txt").write_text("Welcome {{ name }}!", encoding="utf-8")
    mailer, cap = _mailer(template_dir=str(tmp_path))
    await mailer.send("welcome", "sam@example.com", context={"name": "Sam"})
    msg = cap.messages[0]
    assert msg["Subject"] == "Hi sam@example.com"
    assert "Welcome Sam!" in _text(msg)


# --- from_env ---


def test_from_env_reads_variables(monkeypatch):
    monkeypatch.setenv("ASTERION_SMTP_HOST", "smtp.env.com")
    monkeypatch.setenv("ASTERION_SMTP_FROM", "noreply@env.com")
    monkeypatch.setenv("ASTERION_SMTP_PORT", "465")
    monkeypatch.setenv("ASTERION_SMTP_USE_TLS", "true")
    monkeypatch.setenv("ASTERION_INVITE_URL", "https://env/accept?token={token}")

    mailer = SmtpEmailNotifier.from_env()
    assert mailer.host == "smtp.env.com"
    assert mailer.from_addr == "noreply@env.com"
    assert mailer.port == 465
    assert mailer.use_tls is True
    assert mailer.invite_link("Z") == "https://env/accept?token=Z"


def test_from_env_requires_host_and_from(monkeypatch):
    monkeypatch.delenv("ASTERION_SMTP_HOST", raising=False)
    monkeypatch.delenv("ASTERION_SMTP_FROM", raising=False)
    with pytest.raises(ValueError, match="ASTERION_SMTP_HOST"):
        SmtpEmailNotifier.from_env()


# --- default send without aiosmtplib ---


@pytest.mark.asyncio
async def test_default_send_without_aiosmtplib_raises_clear_error(monkeypatch):
    # Simulate aiosmtplib not being installed: a None entry makes
    # ``import aiosmtplib`` raise ImportError.
    monkeypatch.setitem(sys.modules, "aiosmtplib", None)
    mailer = SmtpEmailNotifier(host="h", from_addr="a@b.c")  # no transport
    with pytest.raises(ImportError, match=r"asterion-admin\[email\]"):
        await mailer.send_invite(email="e@x.com", token="T")
