"""
SearchPreferences repository — all database operations for preferences.

Same pattern as UserRepository and ProfileRepository.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users.preferences_models import SearchPreferences


class PreferencesRepository:
    """Encapsulates all database operations for SearchPreferences."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id) -> SearchPreferences | None:
        """Find preferences by the owning user's UUID."""
        stmt = select(SearchPreferences).where(SearchPreferences.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id,
        preferred_gender: str = "any",
        preferred_age_min: int = 18,
        preferred_age_max: int = 99,
        preferred_language: str = "any",
    ) -> SearchPreferences:
        """Create default preferences for a user."""
        prefs = SearchPreferences(
            user_id=user_id,
            preferred_gender=preferred_gender,
            preferred_age_min=preferred_age_min,
            preferred_age_max=preferred_age_max,
            preferred_language=preferred_language,
        )
        self.session.add(prefs)
        await self.session.flush()
        return prefs

    async def update(
        self,
        prefs: SearchPreferences,
        **kwargs,
    ) -> SearchPreferences:
        """Update specific fields on existing preferences."""
        for field, value in kwargs.items():
            if hasattr(prefs, field):
                setattr(prefs, field, value)
        await self.session.flush()
        return prefs

    async def get_or_create(self, user_id) -> tuple[SearchPreferences, bool]:
        """Fetch existing preferences or create with defaults."""
        existing = await self.get_by_user_id(user_id)
        if existing is not None:
            return existing, False
        new_prefs = await self.create(user_id=user_id)
        return new_prefs, True
