"""Data-subject-request (DSAR) log model (roadmap G8).

A small append-mostly register of GDPR data-subject requests (Art. 15 access /
Art. 16 rectification / Art. 17 erasure / Art. 18 restriction / Art. 20
portability): **who** the subject is, **what** was requested, **when**, and the
**result**. It is the accountability record (Art. 5(2)) that a request was
received and how it was handled — distinct from the audit log, which records the
*technical* action (e.g. ``subject_export`` / ``user_anonymize``).

Global/public table: data-subject rights are about a ``users`` row, which lives
in the public schema, not inside any tenant schema.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GUID, GlobalModel


class DataSubjectRequest(GlobalModel):
    __tablename__ = "data_subject_requests"
    __table_args__ = (Index("ix_dsar_subject_created", "subject_user_id", "created_at"),)

    #: The data subject the request is about (a ``users.id``). Kept as an
    #: FK-less column for the same reason the audit actor is: an external
    #: ``UserProvider`` deployment need not have the user in asterion's DB.
    subject_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)

    #: ``access`` | ``export`` | ``rectification`` | ``erasure`` | ``restriction``.
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)

    #: ``received`` | ``completed`` | ``rejected``.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")

    #: The operator/superadmin who logged or fulfilled the request (nullable for
    #: system-initiated entries).
    handled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    #: Free-text result / justification (e.g. why a request was rejected, or the
    #: erasure ticket reference). Never store the exported PII here.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
