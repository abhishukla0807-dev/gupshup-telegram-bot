"""
Moderation repository — database operations for UserBlock.
"""
import uuid
from typing import Set

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.moderation.models import UserBlock


class BlockRepository:
    """Encapsulates all database operations for user-to-user blocking."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def block_user(
        self,
        blocker_id: uuid.UUID,
        blocked_id: uuid.UUID,
        reason: str | None = None,
    ) -> UserBlock:
        """Create a block record idempotently."""
        stmt = select(UserBlock).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        block = UserBlock(
            blocker_id=blocker_id,
            blocked_id=blocked_id,
            reason=reason,
        )
        self.session.add(block)
        await self.session.flush()
        return block

    async def unblock_user(
        self,
        blocker_id: uuid.UUID,
        blocked_id: uuid.UUID,
    ) -> bool:
        """Remove a block record."""
        stmt = select(UserBlock).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()
            return True
        return False

    async def is_blocked_bidirectional(
        self,
        user1_id: uuid.UUID,
        user2_id: uuid.UUID,
    ) -> bool:
        """Check if either user has blocked the other."""
        stmt = select(UserBlock).where(
            or_(
                (UserBlock.blocker_id == user1_id) & (UserBlock.blocked_id == user2_id),
                (UserBlock.blocker_id == user2_id) & (UserBlock.blocked_id == user1_id),
            )
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def get_all_blocked_ids(self, user_id: uuid.UUID) -> Set[uuid.UUID]:
        """Return all user IDs that cannot be matched with this user (blocked or blocker)."""
        stmt = select(UserBlock).where(
            or_(
                UserBlock.blocker_id == user_id,
                UserBlock.blocked_id == user_id,
            )
        )
        result = await self.session.execute(stmt)
        blocked_set: set[uuid.UUID] = set()
        for row in result.scalars():
            if row.blocker_id == user_id:
                blocked_set.add(row.blocked_id)
            else:
                blocked_set.add(row.blocker_id)
        return blocked_set
