"""Resend + SES provider adapters.

Both inherit rendering from BaseEmailNotifier and only implement delivery, so
these tests inject the send callable and assert the provider-shaped payload —
no network, no httpx/boto3 calls.
"""

from __future__ import annotations

import pytest

from asterion.auth.invite import InviteNotifier
from asterion.auth.password_reset import PasswordResetNotifier
from asterion.extensions.email import ResendEmailNotifier, SesEmailNotifier
from asterion.extensions.email.notifier import EmailContent

# --- Resend ---


def test_resend_satisfies_both_protocols():
    mailer = ResendEmailNotifier(api_key="k", from_addr="a@b.c")
    assert isinstance(mailer, PasswordResetNotifier)
    assert isinstance(mailer, InviteNotifier)


@pytest.mark.asyncio
async def test_resend_builds_api_payload():
    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    mailer = ResendEmailNotifier(
        api_key="k",
        from_addr="admin@example.com",
        invite_url="https://app/accept?token={token}",
        app_name="Acme",
        send=fake_send,
    )
    await mailer.send_invite(email="dave@example.com", token="inv-1", tenant_slug="acme")

    assert captured["from"] == "admin@example.com"
    assert captured["to"] == ["dave@example.com"]
    assert "Acme" in captured["subject"]
    assert "inv-1" in captured["text"]
    # Packaged invite template renders HTML too.
    assert "inv-1" in captured["html"]


@pytest.mark.asyncio
async def test_resend_custom_event():
    sent = []

    async def fake_send(payload):
        sent.append(payload)

    mailer = ResendEmailNotifier(api_key="k", from_addr="a@b.c", send=fake_send)
    mailer.register_template(
        "welcome", lambda to, ctx: EmailContent(subject="W", text=f"hi {ctx['n']}")
    )
    await mailer.send("welcome", "x@example.com", context={"n": "Sam"})
    assert sent[0]["subject"] == "W"
    assert sent[0]["text"] == "hi Sam"
    assert "html" not in sent[0]  # text-only event → no html key


def test_resend_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("ASTERION_SMTP_FROM", "a@b.c")
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        ResendEmailNotifier.from_env()


# --- SES ---


def test_ses_satisfies_both_protocols():
    mailer = SesEmailNotifier(from_addr="a@b.c")
    assert isinstance(mailer, PasswordResetNotifier)
    assert isinstance(mailer, InviteNotifier)


@pytest.mark.asyncio
async def test_ses_passes_to_and_content():
    calls = []

    def fake_send(to, content):
        calls.append((to, content))

    mailer = SesEmailNotifier(
        from_addr="admin@example.com",
        reset_url="https://app/reset?token={token}",
        send=fake_send,
    )
    await mailer.send_reset(email="bob@example.com", token="r-9")

    to, content = calls[0]
    assert to == "bob@example.com"
    assert isinstance(content, EmailContent)
    assert "r-9" in content.text
    assert content.html is not None and "r-9" in content.html


def test_ses_from_env_requires_from(monkeypatch):
    monkeypatch.delenv("ASTERION_EMAIL_FROM", raising=False)
    monkeypatch.delenv("ASTERION_SMTP_FROM", raising=False)
    with pytest.raises(ValueError, match="ASTERION_EMAIL_FROM"):
        SesEmailNotifier.from_env()
