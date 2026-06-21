"""Transactional email outbox: enqueue + worker.

Runs on SQLite. The OutboxEmailNotifier enqueues (via a request-scoped session
or a session factory); process_outbox drains the queue through a real notifier
with bounded retries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.extensions.email import (
    EmailOutbox,
    OutboxEmailNotifier,
    SmtpEmailNotifier,
    process_outbox,
)
from asterion.extensions.email.notifier import BaseEmailNotifier
from asterion.models.base import GLOBAL_METADATA


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _inner() -> SmtpEmailNotifier:
    return SmtpEmailNotifier(
        host="h",
        from_addr="admin@example.com",
        invite_url="https://app/accept?token={token}",
        reset_url="https://app/reset?token={token}",
    )


async def _count(factory, **filters) -> int:
    async with factory() as s:
        q = select(func.count(EmailOutbox.id))
        for k, v in filters.items():
            q = q.where(getattr(EmailOutbox, k) == v)
        return (await s.execute(q)).scalar_one()


# --- enqueue ---


@pytest.mark.asyncio
async def test_enqueue_via_session_factory(factory):
    mailer = OutboxEmailNotifier(_inner(), session_factory=factory)
    await mailer.send_invite(email="dave@example.com", token="t1", tenant_slug="acme")

    async with factory() as s:
        row = (await s.execute(select(EmailOutbox))).scalar_one()
    assert row.to_addr == "dave@example.com"
    assert row.status == "pending"
    assert "t1" in row.body_text
    assert row.body_html is not None  # rendered HTML stored too


@pytest.mark.asyncio
async def test_enqueue_uses_request_session_transactionally(factory):
    mailer = OutboxEmailNotifier(_inner())  # no factory — must use request session
    async with factory() as session:
        async with session.begin():
            request = SimpleNamespace(state=SimpleNamespace(db_session=session))
            await mailer.send_reset(email="bob@example.com", token="r1", request=request)
            # Visible within the same transaction, before commit.
            n = (await session.execute(select(func.count(EmailOutbox.id)))).scalar_one()
            assert n == 1
    # Committed with the transaction.
    assert await _count(factory) == 1


@pytest.mark.asyncio
async def test_enqueue_without_session_or_factory_raises():
    mailer = OutboxEmailNotifier(_inner())
    with pytest.raises(RuntimeError, match="no session"):
        await mailer.send_reset(email="x@example.com", token="z")


# --- worker ---


class _RecordingNotifier(BaseEmailNotifier):
    def __init__(self, *, fail_times: int = 0) -> None:
        super().__init__(from_addr="a@b.c")
        self.sent: list[str] = []
        self._fail_times = fail_times
        self._calls = 0

    async def deliver(self, *, to, content, request=None):
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError("smtp down")
        self.sent.append(to)


async def _enqueue(factory, to: str, *, scheduled_at=None):
    async with factory() as s:
        async with s.begin():
            s.add(
                EmailOutbox(
                    to_addr=to,
                    subject="S",
                    body_text="body",
                    status="pending",
                    attempts=0,
                    scheduled_at=scheduled_at or datetime.now(UTC),
                )
            )


@pytest.mark.asyncio
async def test_process_sends_pending(factory):
    await _enqueue(factory, "a@example.com")
    await _enqueue(factory, "b@example.com")
    notifier = _RecordingNotifier()

    async with factory() as s:
        async with s.begin():
            result = await process_outbox(s, notifier)

    assert result == {"sent": 2, "failed": 0, "retried": 0}
    assert set(notifier.sent) == {"a@example.com", "b@example.com"}
    assert await _count(factory, status="sent") == 2


@pytest.mark.asyncio
async def test_process_retries_then_fails(factory):
    await _enqueue(factory, "a@example.com")
    notifier = _RecordingNotifier(fail_times=99)
    now = datetime.now(UTC)

    # First pass: one failure → retried, rescheduled into the future.
    async with factory() as s:
        async with s.begin():
            r1 = await process_outbox(s, notifier, max_attempts=2, now=now)
    assert r1 == {"sent": 0, "failed": 0, "retried": 1}

    # Still pending but scheduled later — not due at `now`.
    async with factory() as s:
        async with s.begin():
            r_due = await process_outbox(s, notifier, max_attempts=2, now=now)
    assert r_due["retried"] == 0  # nothing due yet

    # Far-future pass: second attempt hits max_attempts → failed.
    later = now + timedelta(hours=2)
    async with factory() as s:
        async with s.begin():
            r2 = await process_outbox(s, notifier, max_attempts=2, now=later)
    assert r2 == {"sent": 0, "failed": 1, "retried": 0}
    assert await _count(factory, status="failed") == 1


@pytest.mark.asyncio
async def test_process_skips_future_scheduled(factory):
    await _enqueue(factory, "a@example.com", scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    notifier = _RecordingNotifier()
    async with factory() as s:
        async with s.begin():
            result = await process_outbox(s, notifier)
    assert result["sent"] == 0
    assert notifier.sent == []
