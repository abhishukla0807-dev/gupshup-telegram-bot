"""
User service — business logic layer.

Sits between the bot handlers and the repository. Owns the database
session lifecycle (open → do work → commit/rollback → close).
Handlers never manage transactions themselves.
"""
import logging

from app.infrastructure.database import AsyncSessionLocal
from app.core.users.repository import UserRepository

logger = logging.getLogger(__name__)


class UserService:
    """High-level user operations used by bot handlers."""

    async def register_or_fetch(
        self,
        telegram_id: int,
        first_name: str,
        username: str | None = None,
    ) -> tuple:
        """
        Called on /start. Returns (user, is_new_user).

        Opens its own session, commits on success, rolls back on error.
        """
        async with AsyncSessionLocal() as session:
            try:
                repo = UserRepository(session)
                user, created = await repo.get_or_create(
                    telegram_id=telegram_id,
                    first_name=first_name,
                    username=username,
                )
                await session.commit()

                if created:
                    logger.info("New user registered: telegram_id=%s", telegram_id)
                else:
                    logger.info("Returning user: telegram_id=%s", telegram_id)

                return user, created

            except Exception:
                await session.rollback()
                logger.exception("Error in register_or_fetch for telegram_id=%s", telegram_id)
                raise
