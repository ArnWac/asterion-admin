from datetime import datetime
from sqlalchemy import String, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from adminfoundry.models.base import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    __table_args__ = (
        Index("ix_revoked_tokens_exp", "exp"),
    )

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    exp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
