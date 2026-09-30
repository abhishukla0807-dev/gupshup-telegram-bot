"""
Moderation service — business logic for user blocks and safety checks.

Dual-write strategy: blocks are persisted to PostgreSQL (durable) and mirrored
to Redis SETs (fast) for sub-millisecond lookups during matchmaking.
"""
import logging
from typing import Set

from redis.asyncio import Redis

from app.core.moderation.repository import BlockRepository
from app.core.users.repository import UserRepository
from app.infrastructure.database import AsyncSessionLocal
from app.infrastructure.redis import get_redis_client

logger = logging.getLogger(__name__)

BLOCK_KEY_PREFIX = "user:blocks:"
BLOCK_TTL = 3600  # 1 hour cache TTL


class ModerationService:
    """High-level moderation operations."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _get_client(self) -> Redis:
        return self._redis or get_redis_client()

    async def block_by_telegram_ids(
        self,
        blocker_telegram_id: int,
        blocked_telegram_id: int,
        reason: str | None = None,
    ) -> bool:
        """Block a user by their Telegram ID. Dual-writes to PostgreSQL + Redis."""
        if blocker_telegram_id == blocked_telegram_id:
            return False

        async with AsyncSessionLocal() as session:
            try:
                user_repo = UserRepository(session)
                blocker = await user_repo.get_by_telegram_id(blocker_telegram_id)
                blocked = await user_repo.get_by_telegram_id(blocked_telegram_id)

                if blocker is None or blocked is None:
                    logger.warning(
                        "Cannot block: blocker or blocked user not found in DB (%s -> %s)",
                        blocker_telegram_id,
                        blocked_telegram_id,
                    )
                    return False

                block_repo = BlockRepository(session)
                await block_repo.block_user(
                    blocker_id=blocker.id,
                    blocked_id=blocked.id,
                    reason=reason,
                )
                await session.commit()

                # Mirror to Redis for fast matchmaking lookups
                try:
                    client = self._get_client()
                    await client.sadd(
                        f"{BLOCK_KEY_PREFIX}{blocker_telegram_id}",
                        str(blocked_telegram_id),
                    )
                    await client.sadd(
                        f"{BLOCK_KEY_PREFIX}{blocked_telegram_id}",
                        str(blocker_telegram_id),
                    )
                except Exception:
                    logger.debug("Redis block mirror failed (non-critical)")

                logger.info(
                    "User %s blocked user %s (reason: %s)",
                    blocker_telegram_id,
                    blocked_telegram_id,
                    reason,
                )
                return True
            except Exception:
                await session.rollback()
                logger.exception(
                    "Error blocking user %s by %s",
                    blocked_telegram_id,
                    blocker_telegram_id,
                )
                raise

    async def are_users_mutually_blocked(
        self,
        tg_id_1: int,
        tg_id_2: int,
    ) -> bool:
        """Check if either user has blocked the other."""
        if tg_id_1 == tg_id_2:
            return True

        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            u1 = await user_repo.get_by_telegram_id(tg_id_1)
            u2 = await user_repo.get_by_telegram_id(tg_id_2)
            if u1 is None or u2 is None:
                return False

            block_repo = BlockRepository(session)
            return await block_repo.is_blocked_bidirectional(u1.id, u2.id)

    async def are_users_mutually_blocked_fast(
        self,
        tg_id_a: int,
        tg_id_b: int,
    ) -> bool:
        """
        Fast Redis-backed mutual block check using SISMEMBER.

        Complexity: O(1) per check, ~0.1ms latency.
        Falls through to PostgreSQL if Redis keys are missing.
        """
        try:
            client = self._get_client()
            # Pipeline two SISMEMBER calls into a single round-trip
            pipe = client.pipeline(transaction=False)
            pipe.sismember(f"{BLOCK_KEY_PREFIX}{tg_id_a}", str(tg_id_b))
            pipe.sismember(f"{BLOCK_KEY_PREFIX}{tg_id_b}", str(tg_id_a))
            results = await pipe.execute()

            if results[0] or results[1]:
                return True

            # Check if keys exist at all (cache miss = fall through to DB)
            pipe2 = client.pipeline(transaction=False)
            pipe2.exists(f"{BLOCK_KEY_PREFIX}{tg_id_a}")
            pipe2.exists(f"{BLOCK_KEY_PREFIX}{tg_id_b}")
            exists_results = await pipe2.execute()

            # If both keys exist in Redis and neither contains the other, not blocked
            if exists_results[0] and exists_results[1]:
                return False

        except Exception:
            logger.debug("Redis block check failed, falling through to PostgreSQL")

        # Fallback to durable PostgreSQL check
        return await self.are_users_mutually_blocked(tg_id_a, tg_id_b)

    async def get_blocked_telegram_ids(self, telegram_id: int) -> Set[int]:
        """Fetch all Telegram IDs that this user cannot match with."""
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)
            if user is None:
                return set()

            block_repo = BlockRepository(session)
            blocked_uuids = await block_repo.get_all_blocked_ids(user.id)
            if not blocked_uuids:
                return set()

            # Resolve UUIDs back to telegram_ids
            blocked_tg_ids: set[int] = set()
            for b_uuid in blocked_uuids:
                b_user = await user_repo.get_by_id(b_uuid)
                if b_user:
                    blocked_tg_ids.add(b_user.telegram_id)

            return blocked_tg_ids
