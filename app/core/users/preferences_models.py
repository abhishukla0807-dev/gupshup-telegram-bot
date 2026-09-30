"""
SearchPreferences SQLAlchemy model.

This table answers: "What kind of partner does this user want to be
matched with?" It is deliberately separate from the Profile table:

  - Profile = "Who am I?" (my gender, my age, my language)
  - SearchPreferences = "Who do I want to talk to?" (preferred partner attributes)

A User has exactly one SearchPreferences row. It is created during the
/settings flow (Phase 6) and used by the matching engine (Phase 9)
to find compatible pairs.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class SearchPreferences(Base):
    __tablename__ = "search_preferences"

    # ---------- Primary Key ----------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ---------- Foreign Key → User ----------
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ---------- Partner Preferences ----------
    preferred_gender: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="any",
        comment="any | male | female | other — what gender partner do they want?",
    )

    preferred_age_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=18,
        comment="Minimum partner age (inclusive).",
    )

    preferred_age_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=99,
        comment="Maximum partner age (inclusive).",
    )

    preferred_language: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="any",
        comment="any | en | hi | es | ... — filter partners by language.",
    )

    # ---------- Matching Behavior ----------
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="If False, user is excluded from matchmaking entirely.",
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
    user = relationship(
        "User", backref="search_preferences", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<SearchPreferences user_id={self.user_id} "
            f"gender={self.preferred_gender} age={self.preferred_age_min}-{self.preferred_age_max}>"
        )
