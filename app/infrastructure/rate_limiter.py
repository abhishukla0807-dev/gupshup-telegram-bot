"""
Rate Limiter Infrastructure — Redis Atomic Lua Sliding Window & Token Bucket.

Provides high-performance, concurrency-safe rate limiting:
  1. Chat Messages: Atomic Lua Sliding Window (ZSET)
     - Key: rl:msg:{user_id}
     - Default: 5 messages / 2.0 seconds
     - Enforces fine-grained burst control across horizontal workers.
     - Auto-expires unused keys with PEXPIRE.

  2. /search Command: Atomic Lua Token Bucket (HASH)
     - Key: rl:search:{user_id}
     - Default: 1 attempt / 3.0 seconds
     - Prevents rapid repeated queue hammering and DB lock churn.
     - Auto-expires unused keys.

Architectural Guarantees:
  - 100% outside PostgreSQL hot path (pure Redis).
  - Atomic Lua scripts prevent race conditions across multiple worker processes.
  - Zero Python asyncio.Lock usage.
  - Fail-safe: if Redis is temporarily unavailable, fails open without crashing the bot.
  - Structured logging without ever logging message content.
"""
import logging
import math
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from redis.asyncio import Redis

from app.config.settings import settings
from app.infrastructure.redis import get_redis_client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lua Scripts
# ─────────────────────────────────────────────────────────────────────────────

# Sliding window via ZSET:
# KEYS[1] = rl:msg:{user_id}
# ARGV[1] = now_ms (current timestamp in milliseconds)
# ARGV[2] = window_ms (window duration in milliseconds)
# ARGV[3] = limit (max allowed requests in window)
# ARGV[4] = unique_member (identifier for this request)
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

-- 1. Remove entries older than (now - window)
local clear_before = now - window
redis.call("ZREMRANGEBYSCORE", key, "-inf", clear_before)

-- 2. Count requests in current window
local current_count = redis.call("ZCARD", key)

if current_count < limit then
    -- Allowed: record this request with score = now
    redis.call("ZADD", key, now, member)
    redis.call("PEXPIRE", key, math.ceil(window * 2))
    local remaining = limit - current_count - 1
    return {1, remaining, 0}
else
    -- Rejected: calculate retry_after from the oldest entry in the window
    local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    local retry_after_ms = 0
    if oldest and #oldest >= 2 then
        local oldest_time = tonumber(oldest[2])
        local wait_ms = (oldest_time + window) - now
        if wait_ms > 0 then
            retry_after_ms = wait_ms
        else
            retry_after_ms = 50
        end
    else
        retry_after_ms = window
    end
    -- Keep key alive so fast successive checks don't lose the window boundary
    redis.call("PEXPIRE", key, math.ceil(window * 2))
    return {0, 0, math.ceil(retry_after_ms)}
end
"""

# Token Bucket via HASH:
# KEYS[1] = rl:search:{user_id}
# ARGV[1] = now_ms (current timestamp in milliseconds)
# ARGV[2] = capacity (max tokens bucket can hold)
# ARGV[3] = refill_period_ms (milliseconds required to generate 1 token)
# ARGV[4] = cost (tokens required for this action)
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_period_ms = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local refill_rate = 1.0 / refill_period_ms

local data = redis.call("HMGET", key, "tokens", "last_updated")
local tokens
local last_updated

if not data[1] or not data[2] then
    tokens = capacity
    last_updated = now
else
    tokens = tonumber(data[1])
    last_updated = tonumber(data[2])
    local elapsed = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + (elapsed * refill_rate))
    last_updated = now
end

local allowed = 0
local remaining = 0
local retry_after_ms = 0

if tokens >= cost then
    allowed = 1
    tokens = tokens - cost
    remaining = math.floor(tokens)
    retry_after_ms = 0
else
    allowed = 0
    remaining = 0
    local needed = cost - tokens
    retry_after_ms = math.max(1, math.ceil(needed * refill_period_ms))
end

redis.call("HMSET", key, "tokens", tostring(tokens), "last_updated", tostring(last_updated))

-- Automatically expire unused key (at least 2 full refill periods or 10s)
local ttl_ms = math.max(math.ceil(refill_period_ms * 2), 10000)
redis.call("PEXPIRE", key, ttl_ms)

return {allowed, remaining, retry_after_ms}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate limit evaluation."""
    allowed: bool
    remaining: int
    retry_after: float  # seconds


class RateLimiter:
    """
    High-performance Redis rate limiter with sliding window and token bucket algorithms.
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _get_client(self) -> Redis:
        return self._redis or get_redis_client()

    async def check_chat_rate_limit(
        self,
        user_id: int,
        max_messages: int | None = None,
        window_seconds: float | None = None,
    ) -> RateLimitResult:
        """
        Sliding-window rate limit check for chat messages.

        Default: 5 messages per 2.0 seconds.
        Key: rl:msg:{user_id}
        """
        limit = max_messages if max_messages is not None else settings.RATE_LIMIT_CHAT_MAX_MESSAGES
        window = window_seconds if window_seconds is not None else settings.RATE_LIMIT_CHAT_WINDOW_SECONDS
        key = f"rl:msg:{user_id}"

        now_ms = int(time.time() * 1000)
        window_ms = int(window * 1000)
        req_id = f"{now_ms}:{uuid.uuid4().hex[:6]}"

        try:
            client = self._get_client()
            res = await client.eval(
                SLIDING_WINDOW_LUA,
                1,
                key,
                str(now_ms),
                str(window_ms),
                str(limit),
                req_id,
            )

            allowed = bool(res[0] == 1)
            remaining = int(res[1])
            retry_after = round(float(res[2]) / 1000.0, 2)

            if not allowed:
                logger.warning(
                    "Rate limit exceeded [chat]: user_id=%s limit=%d window=%.1fs retry_after=%.2fs",
                    user_id,
                    limit,
                    window,
                    retry_after,
                )
            else:
                logger.debug(
                    "Rate limit pass [chat]: user_id=%s remaining=%d",
                    user_id,
                    remaining,
                )

            return RateLimitResult(allowed=allowed, remaining=remaining, retry_after=retry_after)

        except Exception as e:
            # Fail safely: if Redis is temporarily unreachable, do not block chat messages
            logger.error("Rate limiter Redis failure on check_chat_rate_limit (failing open): %s", e)
            return RateLimitResult(allowed=True, remaining=1, retry_after=0.0)

    async def check_search_rate_limit(
        self,
        user_id: int,
        capacity: int | None = None,
        refill_period_seconds: float | None = None,
    ) -> RateLimitResult:
        """
        Token-bucket rate limit check for /search command and search button.

        Default: 1 attempt per 3.0 seconds.
        Key: rl:search:{user_id}
        """
        cap = capacity if capacity is not None else settings.RATE_LIMIT_SEARCH_CAPACITY
        refill_sec = (
            refill_period_seconds
            if refill_period_seconds is not None
            else settings.RATE_LIMIT_SEARCH_REFILL_PERIOD_SECONDS
        )
        key = f"rl:search:{user_id}"

        now_ms = int(time.time() * 1000)
        refill_ms = int(refill_sec * 1000)
        cost = 1.0

        try:
            client = self._get_client()
            res = await client.eval(
                TOKEN_BUCKET_LUA,
                1,
                key,
                str(now_ms),
                str(cap),
                str(refill_ms),
                str(cost),
            )

            allowed = bool(res[0] == 1)
            remaining = int(res[1])
            retry_after = round(float(res[2]) / 1000.0, 2)

            if not allowed:
                logger.warning(
                    "Rate limit exceeded [search]: user_id=%s capacity=%d refill_period=%.1fs retry_after=%.2fs",
                    user_id,
                    cap,
                    refill_sec,
                    retry_after,
                )
            else:
                logger.debug(
                    "Rate limit pass [search]: user_id=%s remaining=%d",
                    user_id,
                    remaining,
                )

            return RateLimitResult(allowed=allowed, remaining=remaining, retry_after=retry_after)

        except Exception as e:
            # Fail safely: do not block search if Redis has transient glitch
            logger.error("Rate limiter Redis failure on check_search_rate_limit (failing open): %s", e)
            return RateLimitResult(allowed=True, remaining=1, retry_after=0.0)

    async def reset_limits(self, user_id: int) -> None:
        """Helper to reset rate limits for a user (used primarily in test suites)."""
        try:
            client = self._get_client()
            await client.delete(f"rl:msg:{user_id}", f"rl:search:{user_id}")
        except Exception as e:
            logger.debug("Failed to reset rate limits for user %s: %s", user_id, e)


# Global singleton instance
rate_limiter = RateLimiter()
