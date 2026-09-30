"""
Redis infrastructure — async connection pooling, client provider, and distributed locking.

Redis is used for:
  1. User volatile state tracking (IDLE, SEARCHING, CHATTING)
  2. Matchmaking waiting queues and candidate pools (Phase 8-9)
  3. Distributed locks to prevent concurrency race conditions (Phase 7-9)
  4. Session message routing and partner mappings (Phase 10)
  5. Rate limiting counters and sliding windows (Phase 14)
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import LockError

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Global connection pool singleton
_redis_pool: ConnectionPool | None = None


def get_redis_pool() -> ConnectionPool:
    """Get or create the global Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=200,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,

        )
        logger.info("Redis connection pool initialized for %s", settings.REDIS_URL)
    return _redis_pool


def get_redis_client() -> Redis:
    """Get an async Redis client backed by the global connection pool."""
    pool = get_redis_pool()
    return Redis(connection_pool=pool)


async def init_redis() -> bool:
    """
    Test and initialize the Redis connection at application startup.

    Returns True if Redis is reachable, raises ConnectionError if not.
    """
    client = get_redis_client()
    try:
        pong = await client.ping()
        logger.info("Redis connection verified: ping response=%s", pong)
        return bool(pong)
    except Exception as e:
        logger.error("Failed to connect to Redis at %s: %s", settings.REDIS_URL, e)
        raise


async def close_redis() -> None:
    """Gracefully close the global Redis connection pool at shutdown."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None
        logger.info("Redis connection pool disconnected.")


@asynccontextmanager
async def redis_lock(
    name: str,
    timeout: float = 10.0,
    blocking_timeout: float = 5.0,
) -> AsyncGenerator[bool, None]:
    """
    Async distributed lock using Redis.

    Prevents race conditions in concurrent operations such as:
      - Double-entry into matchmaking queue
      - Simultaneous matching of the same user by multiple workers
      - Race conditions during chat disconnect / session transition

    Usage:
        async with redis_lock(f"lock:user:{telegram_id}") as acquired:
            if not acquired:
                # Could not acquire lock within blocking_timeout
                return
            # Critical section
    """
    client = get_redis_client()
    lock = client.lock(
        name=f"lock:{name}",
        timeout=timeout,
        blocking_timeout=blocking_timeout,
    )
    acquired = False
    try:
        acquired = await lock.acquire()
        yield acquired
    except LockError as e:
        logger.warning("Lock error for %s: %s", name, e)
        yield False
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception as e:
                logger.debug("Error releasing lock %s: %s", name, e)
