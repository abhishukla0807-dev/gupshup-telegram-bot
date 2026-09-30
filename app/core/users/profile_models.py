"""
Profile SQLAlchemy model.

Separated from the User model by design: the User table is about *identity*
(who is this person?), while the Profile table is about *matchmaking attributes*
(what are they looking for?).

A User has exactly one Profile. The Profile is created during onboarding
(Phase 5) and can be updated later via /edit (Phase 6).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    # ---------- Primary Key ----------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ---------- Foreign Key → User ----------
    # One-to-one: each user has exactly one profile.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ---------- Matchmaking Attributes ----------
    gender: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="male | female | other",
    )

    age: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    language: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="en",
        comment="ISO 639-1 language code: en, hi, es, etc.",
    )

    bio: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional short bio shown to match partner.",
    )

    # ---------- Onboarding Tracking ----------
    is_complete: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="True once the user finishes the full onboarding flow.",
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

    # ---------- Relationship ----------
    user = relationship("User", backref="profile", uselist=False, lazy="selectin")

    def __repr__(self) -> str:
        return f"<Profile user_id={self.user_id} gender={self.gender} age={self.age}>"
