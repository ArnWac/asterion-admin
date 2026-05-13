from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from adminfoundry.models.base import TimestampedBase


class Note(TimestampedBase):
    __tablename__ = "notes"

    title:   Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
