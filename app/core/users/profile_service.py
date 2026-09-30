"""
Profile service — business logic for profile creation and updates.

Owns the session lifecycle just like UserService. Called by the
onboarding FSM handler and the /edit handler.
"""
import logging

from app.infrastructure.database import AsyncSessionLocal
from app.core.users.repository import UserRepository
from app.core.users.profile_repository import ProfileRepository

logger = logging.getLogger(__name__)


class ProfileService:
    """High-level profile operations used by bot handlers."""

    async def create_profile(
        self,
        telegram_id: int,
        gender: str,
        age: int,
        language: str,
        bio: str | None = None,
    ) -> object:
        """
        Create a profile for a user identified by their telegram_id.

        Called at the end of the onboarding FSM flow after all data
        has been collected step by step.
        """
        async with AsyncSessionLocal() as session:
            try:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_telegram_id(telegram_id)
                if user is None:
                    raise ValueError(f"No user found for telegram_id={telegram_id}")

                profile_repo = ProfileRepository(session)

                # Check if profile already exists (re-onboarding)
                existing = await profile_repo.get_by_user_id(user.id)
                if existing:
                    profile = await profile_repo.update(
                        existing,
                        gender=gender,
                        age=age,
                        language=language,
                        bio=bio,
                        is_complete=True,
                    )
                    logger.info("Profile updated for telegram_id=%s", telegram_id)
                else:
                    profile = await profile_repo.create(
                        user_id=user.id,
                        gender=gender,
                        age=age,
                        language=language,
                        bio=bio,
                    )
                    logger.info("Profile created for telegram_id=%s", telegram_id)

                await session.commit()
                return profile

            except Exception:
                await session.rollback()
                logger.exception(
                    "Error creating/updating profile for telegram_id=%s", telegram_id
                )
                raise

    async def get_profile(self, telegram_id: int):
        """Fetch a user's profile by telegram_id. Returns None if no profile."""
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)
            if user is None:
                return None

            profile_repo = ProfileRepository(session)
            return await profile_repo.get_by_user_id(user.id)

    async def has_complete_profile(self, telegram_id: int) -> bool:
        """Check if a user has finished onboarding."""
        profile = await self.get_profile(telegram_id)
        return profile is not None and profile.is_complete

    async def update_profile(
        self,
        telegram_id: int,
        **kwargs,
    ) -> object:
        """
        Update specific fields on an existing user's profile.

        Used by the /edit command for single-field modifications.
        """
        async with AsyncSessionLocal() as session:
            try:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_telegram_id(telegram_id)
                if user is None:
                    raise ValueError(f"No user found for telegram_id={telegram_id}")

                profile_repo = ProfileRepository(session)
                profile = await profile_repo.get_by_user_id(user.id)
                if profile is None:
                    raise ValueError(f"No profile found for user_id={user.id}")

                updated = await profile_repo.update(profile, **kwargs)
                await session.commit()
                logger.info(
                    "Profile updated for telegram_id=%s with fields: %s",
                    telegram_id,
                    list(kwargs.keys()),
                )
                return updated
            except Exception:
                await session.rollback()
                logger.exception(
                    "Error updating profile for telegram_id=%s", telegram_id
                )
                raise

