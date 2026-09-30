"""
ChatSession and UserActiveSession SQLAlchemy models.

Durable PostgreSQL representation of chat sessions.
Guarantees the distributed invariant:
  A user can have AT MOST ONE active chat session across the entire system.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    # ---------- Primary Key ----------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ---------- Participants ----------
    user1_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user2_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ---------- Lifecycle ----------
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
        index=True,
        comment="active | ended | terminated",
    )
    ended_reason: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="user_left | next | blocked | timeout | admin",
    )

    # ---------- Timestamps ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ---------- Relationships ----------
    user1 = relationship("User", foreign_keys=[user1_id], lazy="selectin")
    user2 = relationship("User", foreign_keys=[user2_id], lazy="selectin")

    __table_args__ = (
        CheckConstraint("user1_id != user2_id", name="chk_different_users"),
        Index("ix_chat_sessions_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatSession id={self.id} user1={self.user1_id} "
            f"user2={self.user2_id} status={self.status}>"
        )


class UserActiveSession(Base):
    """
    Tracks which session a user is currently engaged in.

    By using user_id as the PRIMARY KEY, PostgreSQL mathematically guarantees
    that no user can ever have more than ONE active session. Any attempt
    to insert a duplicate active session will abort the transaction with a Unique Violation.
    """
    __tablename__ = "user_active_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("ChatSession", lazy="selectin")
    user = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        return f"<UserActiveSession user={self.user_id} session={self.session_id}>"
