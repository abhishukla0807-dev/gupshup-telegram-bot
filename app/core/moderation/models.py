"""
UserBlock SQLAlchemy model.

Stores persistent user-to-user blocks so that blocked pairs
are never matched again by the matchmaking engine.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class UserBlock(Base):
    __tablename__ = "user_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    blocked_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Optional reason: harassment | spam | unwanted | other",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    blocker = relationship("User", foreign_keys=[blocker_id], lazy="selectin")
    blocked = relationship("User", foreign_keys=[blocked_id], lazy="selectin")

    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_blocker_blocked"),
        CheckConstraint("blocker_id != blocked_id", name="chk_cannot_block_self"),
    )

    def __repr__(self) -> str:
        return f"<UserBlock blocker={self.blocker_id} blocked={self.blocked_id}>"
