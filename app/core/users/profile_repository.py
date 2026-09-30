"""
Profile repository — all database operations for the Profile model.

Same pattern as UserRepository: this is the only layer that touches
the database for profiles. Service and handlers never write queries.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users.profile_models import Profile


class ProfileRepository:
    """Encapsulates all database operations for the Profile model."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id) -> Profile | None:
        """Find a profile by the owning user's UUID."""
        stmt = select(Profile).where(Profile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id,
        gender: str,
        age: int,
        language: str,
        bio: str | None = None,
    ) -> Profile:
        """Create a new profile for a user."""
        profile = Profile(
            user_id=user_id,
            gender=gender,
            age=age,
            language=language,
            bio=bio,
            is_complete=True,
        )
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def update(
        self,
        profile: Profile,
        **kwargs,
    ) -> Profile:
        """
        Update specific fields on an existing profile.

        Usage: await repo.update(profile, gender="female", age=25)
        """
        for field, value in kwargs.items():
            if hasattr(profile, field):
                setattr(profile, field, value)
        await self.session.flush()
        return profile
