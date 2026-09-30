"""
Comprehensive Unit & Concurrency Test Suite for Redis Rate Limiters.

Verifies:
  1. Sliding Window: Normal traffic & sequential limits (rl:msg:{user_id}).
  2. Sliding Window: Burst rejection & accurate retry_after.
  3. Sliding Window: Expiry and window progression.
  4. Token Bucket: Normal single usage & burst rejection (rl:search:{user_id}).
  5. Token Bucket: Token refill timing & retry_after accuracy.
  6. High-concurrency race condition safety (20+ tasks via asyncio.gather).
  7. Key TTL verification (keys auto-expire, preventing unbounded memory growth).
  8. Fail-safe degradation (returns allowed=True when Redis fails, no bot crash).
"""
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from app.infrastructure.rate_limiter import RateLimiter, rate_limiter
from app.infrastructure.redis import close_redis, get_redis_client, init_redis


# Tests use get_redis_client directly



@pytest.mark.asyncio
async def test_sliding_window_normal_traffic():
    """Verify normal traffic below the threshold is accepted with decreasing remaining quota."""
    user_id = 80001
    await rate_limiter.reset_limits(user_id)

    # Limit: 5 messages per 2.0s
    for i in range(5):
        res = await rate_limiter.check_chat_rate_limit(user_id, max_messages=5, window_seconds=2.0)
        assert res.allowed is True
        assert res.remaining == 5 - i - 1
        assert res.retry_after == 0.0

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_sliding_window_burst_rejection_and_retry_after():
    """Verify bursts exceeding threshold are immediately rejected with positive retry_after."""
    user_id = 80002
    await rate_limiter.reset_limits(user_id)

    # Exhaust quota (5 requests)
    for _ in range(5):
        res = await rate_limiter.check_chat_rate_limit(user_id, max_messages=5, window_seconds=2.0)
        assert res.allowed is True

    # 6th request must be rejected
    rejected_res = await rate_limiter.check_chat_rate_limit(user_id, max_messages=5, window_seconds=2.0)
    assert rejected_res.allowed is False
    assert rejected_res.remaining == 0
    assert rejected_res.retry_after > 0.0
    assert rejected_res.retry_after <= 2.0

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_sliding_window_window_progression():
    """Verify rate limit unlocks automatically as older requests exit the sliding window."""
    user_id = 80003
    await rate_limiter.reset_limits(user_id)

    # Use a small 0.4s window with 2 messages limit
    res1 = await rate_limiter.check_chat_rate_limit(user_id, max_messages=2, window_seconds=0.4)
    res2 = await rate_limiter.check_chat_rate_limit(user_id, max_messages=2, window_seconds=0.4)
    res3 = await rate_limiter.check_chat_rate_limit(user_id, max_messages=2, window_seconds=0.4)

    assert res1.allowed is True
    assert res2.allowed is True
    assert res3.allowed is False
    assert res3.retry_after > 0.0

    # Wait for the sliding window to advance
    await asyncio.sleep(res3.retry_after + 0.05)

    # Next request should now be accepted
    res4 = await rate_limiter.check_chat_rate_limit(user_id, max_messages=2, window_seconds=0.4)
    assert res4.allowed is True

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_token_bucket_search_rate_limiting():
    """Verify /search token bucket allows 1 attempt and rejects immediate repeats."""
    user_id = 80004
    await rate_limiter.reset_limits(user_id)

    # 1 attempt / 1.0 second (custom for fast test)
    res1 = await rate_limiter.check_search_rate_limit(user_id, capacity=1, refill_period_seconds=1.0)
    assert res1.allowed is True
    assert res1.remaining == 0
    assert res1.retry_after == 0.0

    # Immediate second attempt must be rejected
    res2 = await rate_limiter.check_search_rate_limit(user_id, capacity=1, refill_period_seconds=1.0)
    assert res2.allowed is False
    assert res2.remaining == 0
    assert 0.0 < res2.retry_after <= 1.0

    # Wait for refill
    await asyncio.sleep(res2.retry_after + 0.05)

    # Third attempt after refill must be allowed
    res3 = await rate_limiter.check_search_rate_limit(user_id, capacity=1, refill_period_seconds=1.0)
    assert res3.allowed is True

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_concurrent_requests_atomicity():
    """Verify atomic Lua execution under high concurrency contention (no race conditions)."""
    user_id = 80005
    await rate_limiter.reset_limits(user_id)

    # 20 concurrent chat message requests fired simultaneously for limit=5
    results = await asyncio.gather(*[
        rate_limiter.check_chat_rate_limit(user_id, max_messages=5, window_seconds=2.0)
        for _ in range(20)
    ])

    allowed_count = sum(1 for r in results if r.allowed)
    rejected_count = sum(1 for r in results if not r.allowed)

    # Exactly 5 must be allowed, exactly 15 must be rejected
    assert allowed_count == 5
    assert rejected_count == 15

    # 10 concurrent search requests fired simultaneously for capacity=1
    search_results = await asyncio.gather(*[
        rate_limiter.check_search_rate_limit(user_id, capacity=1, refill_period_seconds=3.0)
        for _ in range(10)
    ])

    search_allowed = sum(1 for r in search_results if r.allowed)
    search_rejected = sum(1 for r in search_results if not r.allowed)

    # Exactly 1 allowed, 9 rejected
    assert search_allowed == 1
    assert search_rejected == 9

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_key_ttl_and_auto_expiration():
    """Verify rate-limit keys have valid TTLs and do not persist indefinitely in Redis."""
    user_id = 80006
    await rate_limiter.reset_limits(user_id)
    client = get_redis_client()

    await rate_limiter.check_chat_rate_limit(user_id, max_messages=5, window_seconds=2.0)
    await rate_limiter.check_search_rate_limit(user_id, capacity=1, refill_period_seconds=3.0)

    # Check that keys have positive TTL
    msg_ttl = await client.pttl(f"rl:msg:{user_id}")
    search_ttl = await client.pttl(f"rl:search:{user_id}")

    assert msg_ttl > 0, "Chat message rate-limit key must have a positive TTL"
    assert search_ttl > 0, "Search rate-limit key must have a positive TTL"

    await rate_limiter.reset_limits(user_id)


@pytest.mark.asyncio
async def test_fail_safe_behavior_on_redis_error():
    """Verify rate limiter fails open (allowed=True) without crashing when Redis fails."""
    broken_limiter = RateLimiter()
    broken_limiter._get_client = lambda: AsyncMock(eval=AsyncMock(side_effect=Exception("Redis connection lost")))

    # Must return allowed=True without raising an exception
    chat_res = await broken_limiter.check_chat_rate_limit(80007)
    assert chat_res.allowed is True
    assert chat_res.remaining == 1
    assert chat_res.retry_after == 0.0

    search_res = await broken_limiter.check_search_rate_limit(80007)
    assert search_res.allowed is True
    assert search_res.remaining == 1
    assert search_res.retry_after == 0.0


if __name__ == "__main__":
    async def run_standalone():
        print("Running rate limiter test suite...")
        await init_redis()
        await test_sliding_window_normal_traffic()
        print("[PASS] Sliding window normal traffic")
        await test_sliding_window_burst_rejection_and_retry_after()
        print("[PASS] Sliding window burst rejection & retry_after")
        await test_sliding_window_window_progression()
        print("[PASS] Sliding window window progression")
        await test_token_bucket_search_rate_limiting()
        print("[PASS] Token bucket search rate limiting")
        await test_concurrent_requests_atomicity()
        print("[PASS] High concurrency atomicity & race condition safety")
        await test_key_ttl_and_auto_expiration()
        print("[PASS] Key TTL & auto-expiration")
        await test_fail_safe_behavior_on_redis_error()
        print("[PASS] Fail-safe behavior on Redis error")
        await close_redis()
        print("\nALL RATE LIMITER TESTS PASSED 100%!")

    asyncio.run(run_standalone())
