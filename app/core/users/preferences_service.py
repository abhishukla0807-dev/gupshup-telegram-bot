"""
SearchPreferences service — business logic for managing search preferences.

Owns the session lifecycle. Called by the /settings handler.
"""
import logging

from app.infrastructure.database import AsyncSessionLocal
from app.core.users.repository import UserRepository
from app.core.users.preferences_repository import PreferencesRepository

logger = logging.getLogger(__name__)


class PreferencesService:
    """High-level operations for search preferences."""

    async def get_preferences(self, telegram_id: int):
        """Fetch a user's search preferences. Returns None if user not found."""
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)
            if user is None:
                return None

            prefs_repo = PreferencesRepository(session)
            prefs, _ = await prefs_repo.get_or_create(user.id)
            await session.commit()
            return prefs

    async def update_preferences(
        self,
        telegram_id: int,
        preferred_gender: str | None = None,
        preferred_age_min: int | None = None,
        preferred_age_max: int | None = None,
        preferred_language: str | None = None,
    ) -> object:
        """
        Update a user's search preferences.

        Only the fields that are not None get updated.
        """
        async with AsyncSessionLocal() as session:
            try:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_telegram_id(telegram_id)
                if user is None:
                    raise ValueError(f"No user found for telegram_id={telegram_id}")

                prefs_repo = PreferencesRepository(session)
                prefs, _ = await prefs_repo.get_or_create(user.id)

                # Build kwargs for only the fields that were provided
                updates = {}
                if preferred_gender is not None:
                    updates["preferred_gender"] = preferred_gender
                if preferred_age_min is not None:
                    updates["preferred_age_min"] = preferred_age_min
                if preferred_age_max is not None:
                    updates["preferred_age_max"] = preferred_age_max
                if preferred_language is not None:
                    updates["preferred_language"] = preferred_language

                if updates:
                    prefs = await prefs_repo.update(prefs, **updates)

                await session.commit()
                logger.info(
                    "Preferences updated for telegram_id=%s: %s",
                    telegram_id,
                    updates,
                )
                return prefs

            except Exception:
                await session.rollback()
                logger.exception(
                    "Error updating preferences for telegram_id=%s", telegram_id
                )
                raise
