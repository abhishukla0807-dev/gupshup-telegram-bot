"""
User repository — the ONLY layer that talks to the database for users.

Every database query related to users lives here. The service layer and
bot handlers never write raw SQL or touch the session directly.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users.models import User


class UserRepository:
    """Encapsulates all database operations for the User model."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Find a user by their Telegram ID. Returns None if not found."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id) -> User | None:
        """Find a user by their UUID primary key. Returns None if not found."""
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


    async def create(
        self,
        telegram_id: int,
        first_name: str,
        username: str | None = None,
    ) -> User:
        """Create a new user and flush to get the generated UUID back."""
        user = User(
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
        )
        self.session.add(user)
        await self.session.flush()  # assigns the UUID without committing
        return user

    async def get_or_create(
        self,
        telegram_id: int,
        first_name: str,
        username: str | None = None,
    ) -> tuple[User, bool]:
        """
        Return (user, created) — fetch existing or insert new.

        This is the core operation behind /start: idempotent user creation.
        """
        existing = await self.get_by_telegram_id(telegram_id)
        if existing is not None:
            return existing, False

        new_user = await self.create(
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
        )
        return new_user, True
