"""
User SQLAlchemy model.

This is the core identity table. Every person who interacts with the bot
gets exactly one row here. The real telegram_id is stored privately and
never exposed to other users — only the random UUID is used internally
for cross-referencing sessions, matches, and reports.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class User(Base):
    __tablename__ = "users"

    # ---------- Primary Identity ----------
    # Random UUID — the internal ID used across all tables.
    # Never guessable, never sequential.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # The real Telegram user ID. Stored privately, never shared.
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    # ---------- Profile Basics ----------
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    first_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="Anonymous",
    )

    # ---------- Moderation ----------
    is_banned: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # ---------- Timestamps ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id}>"
