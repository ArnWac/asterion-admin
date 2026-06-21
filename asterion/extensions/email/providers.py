"""Transactional-email provider adapters.

Concrete :class:`~asterion.extensions.email.notifier.BaseEmailNotifier`
subclasses that deliver over a provider's HTTP/SDK API instead of SMTP. They
inherit all rendering (reset / invite / custom events, Jinja templates) and
only implement :meth:`deliver`, so a host app swaps transport without changing
anything else::

    from asterion import create_admin
    from asterion.extensions.email import ResendEmailNotifier

    mailer = ResendEmailNotifier.from_env()
    app = create_admin(config=..., password_reset_notifier=mailer, invite_notifier=mailer)

Both adapters take an injectable client (``client=`` / ``send=``) so tests run
without network or the provider SDK installed; the real dependency
(``httpx`` / ``boto3``) is imported lazily on first send.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from asterion.extensions.email.notifier import BaseEmailNotifier, EmailContent, _env

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request


# ---------------------------------------------------------------------------
# Resend (https://resend.com) — JSON HTTP API
# ---------------------------------------------------------------------------

#: ``async (payload: dict) -> None`` — sends one rendered email. Injected in
#: tests; the default posts to the Resend API via httpx.
ResendSender = Callable[[dict[str, Any]], Awaitable[None]]


class ResendEmailNotifier(BaseEmailNotifier):
    """Deliver via Resend's ``POST /emails`` JSON API (needs ``httpx``)."""

    API_URL = "https://api.resend.com/emails"

    def __init__(
        self,
        *,
        api_key: str,
        from_addr: str,
        app_name: str = "Admin",
        reset_url: str | None = None,
        invite_url: str | None = None,
        templates: dict | None = None,
        template_dir: str | os.PathLike[str] | None = None,
        timeout: float = 10.0,
        send: ResendSender | None = None,
    ) -> None:
        super().__init__(
            from_addr=from_addr,
            app_name=app_name,
            reset_url=reset_url,
            invite_url=invite_url,
            templates=templates,
            template_dir=template_dir,
        )
        self.api_key = api_key
        self.timeout = timeout
        self._send = send

    @classmethod
    def from_env(cls, *, send: ResendSender | None = None) -> ResendEmailNotifier:
        """Build from ``RESEND_API_KEY`` + ``ASTERION_SMTP_FROM`` (reused as the
        From address) and the shared ``ASTERION_*_URL`` / template env vars."""
        api_key = _env("RESEND_API_KEY")
        from_addr = _env("ASTERION_EMAIL_FROM") or _env("ASTERION_SMTP_FROM")
        if not api_key or not from_addr:
            raise ValueError(
                "ResendEmailNotifier.from_env() requires RESEND_API_KEY and "
                "ASTERION_EMAIL_FROM (or ASTERION_SMTP_FROM)."
            )
        return cls(
            api_key=api_key,
            from_addr=from_addr,
            app_name=_env("ASTERION_SMTP_APP_NAME", "Admin"),  # type: ignore[arg-type]
            reset_url=_env("ASTERION_RESET_URL"),
            invite_url=_env("ASTERION_INVITE_URL"),
            template_dir=_env("ASTERION_EMAIL_TEMPLATE_DIR"),
            send=send,
        )

    def _payload(self, to: str, content: EmailContent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from": self.from_addr,
            "to": [to],
            "subject": content.subject,
            "text": content.text,
        }
        if content.html is not None:
            payload["html"] = content.html
        return payload

    async def deliver(
        self,
        *,
        to: str,
        content: EmailContent,
        request: Request | None = None,
    ) -> None:
        payload = self._payload(to, content)
        if self._send is not None:
            await self._send(payload)
            return
        await self._default_send(payload)

    async def _default_send(self, payload: dict[str, Any]) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "ResendEmailNotifier needs httpx. Install the extra:\n"
                "    pip install asterion-admin[email-resend]\n"
                "or pass a custom send= callable."
            ) from exc

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()


# ---------------------------------------------------------------------------
# Amazon SES — boto3 SESv2
# ---------------------------------------------------------------------------

#: ``(to, content) -> None`` — sends one rendered email. Injected in tests; the
#: default dispatches a boto3 SESv2 ``send_email`` via ``asyncio.to_thread``.
SesSender = Callable[[str, EmailContent], None]


class SesEmailNotifier(BaseEmailNotifier):
    """Deliver via Amazon SES (boto3 SESv2). ``boto3`` is sync, so the call is
    dispatched through ``asyncio.to_thread`` — same approach as the
    ``storage_s3`` backend."""

    def __init__(
        self,
        *,
        from_addr: str,
        region_name: str | None = None,
        app_name: str = "Admin",
        reset_url: str | None = None,
        invite_url: str | None = None,
        templates: dict | None = None,
        template_dir: str | os.PathLike[str] | None = None,
        send: SesSender | None = None,
    ) -> None:
        super().__init__(
            from_addr=from_addr,
            app_name=app_name,
            reset_url=reset_url,
            invite_url=invite_url,
            templates=templates,
            template_dir=template_dir,
        )
        self.region_name = region_name
        self._send = send
        self._client: Any = None

    @classmethod
    def from_env(cls, *, send: SesSender | None = None) -> SesEmailNotifier:
        from_addr = _env("ASTERION_EMAIL_FROM") or _env("ASTERION_SMTP_FROM")
        if not from_addr:
            raise ValueError(
                "SesEmailNotifier.from_env() requires ASTERION_EMAIL_FROM (or ASTERION_SMTP_FROM)."
            )
        return cls(
            from_addr=from_addr,
            region_name=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION"),
            app_name=_env("ASTERION_SMTP_APP_NAME", "Admin"),  # type: ignore[arg-type]
            reset_url=_env("ASTERION_RESET_URL"),
            invite_url=_env("ASTERION_INVITE_URL"),
            template_dir=_env("ASTERION_EMAIL_TEMPLATE_DIR"),
            send=send,
        )

    async def deliver(
        self,
        *,
        to: str,
        content: EmailContent,
        request: Request | None = None,
    ) -> None:
        if self._send is not None:
            await asyncio.to_thread(self._send, to, content)
            return
        await asyncio.to_thread(self._default_send, to, content)

    def _ses_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - only without the extra
                raise ImportError(
                    "SesEmailNotifier needs boto3. Install the extra:\n"
                    "    pip install asterion-admin[email-ses]\n"
                    "or pass a custom send= callable."
                ) from exc
            self._client = boto3.client("sesv2", region_name=self.region_name)
        return self._client

    def _default_send(self, to: str, content: EmailContent) -> None:
        body: dict[str, Any] = {"Text": {"Data": content.text, "Charset": "UTF-8"}}
        if content.html is not None:
            body["Html"] = {"Data": content.html, "Charset": "UTF-8"}
        self._ses_client().send_email(
            FromEmailAddress=self.from_addr,
            Destination={"ToAddresses": [to]},
            Content={
                "Simple": {"Subject": {"Data": content.subject, "Charset": "UTF-8"}, "Body": body}
            },
        )
