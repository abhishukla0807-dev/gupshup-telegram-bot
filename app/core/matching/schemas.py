"""
Matchmaking schemas — data models for queue candidates and matching criteria.
"""
from typing import List

from pydantic import BaseModel, Field


class QueueCandidate(BaseModel):
    """
    Cached candidate metadata stored in Redis while waiting in the matchmaking queue.

    Serializing this into Redis on queue entry avoids repeated PostgreSQL queries
    during candidate evaluation in the matchmaking worker (Phase 9).
    """
    telegram_id: int
    user_uuid: str = Field(
        default="",
        description="PostgreSQL UUID for this user — avoids SELECT during session handshake",
    )
    gender: str
    age: int
    language: str
    interests: List[str] = Field(
        default_factory=list,
        description="User's interest tags for Jaccard scoring",
    )
    location: str = Field(
        default="",
        description="User's location string (city/country) for proximity scoring",
    )
    media_enabled: bool = Field(
        default=True,
        description="Whether user allows media exchange in chats",
    )
    preferred_gender: str = "any"
    preferred_age_min: int = 18
    preferred_age_max: int = 99
    preferred_language: str = "any"
    joined_at: float = Field(description="Epoch timestamp when user joined queue")

    def matches(self, other: "QueueCandidate") -> bool:
        """
        Check mutual compatibility between two candidates:
          1. My attributes satisfy their preferences
          2. Their attributes satisfy my preferences
        """
        # Mutual Gender check
        if self.preferred_gender != "any" and self.preferred_gender != other.gender:
            return False
        if other.preferred_gender != "any" and other.preferred_gender != self.gender:
            return False

        # Mutual Age check
        if not (self.preferred_age_min <= other.age <= self.preferred_age_max):
            return False
        if not (other.preferred_age_min <= self.age <= other.preferred_age_max):
            return False

        # Mutual Language check
        if (
            self.preferred_language != "any"
            and other.preferred_language != "any"
            and self.preferred_language != other.preferred_language
        ):
            # If both specified explicit languages and they differ, no match
            return False
        if self.preferred_language != "any" and self.preferred_language != other.language:
            return False
        if other.preferred_language != "any" and other.preferred_language != self.language:
            return False

        return True
