# High-Performance Atomic Redis Rate Limiting

## 1. Overview & Problem Statement

In an anonymous 1-on-1 Telegram matchmaking service, rate limiting is a **critical security and operational safeguard**:
1. **Telegram API Rate Limit Protection**: Telegram imposes hard limits on bot message dispatching (approx. 30 messages/second globally and 1 message/second per private chat). A malicious user or script flooding messages in an active conversation will trigger HTTP `429 Too Many Requests`, throttling the bot globally and degrading the experience for all users.
2. **Harassment & Partner Flooding Prevention**: Without rate limiting, a user can spam hundreds of messages, photos, or voice notes per second into their partner's chat before the partner can run `/block` or `/end`.
3. **Queue & Handshake Protection**: Rapidly spamming `/search` or tapping search buttons creates database lock churn, candidate cache overwrite races, and Redis ZSET contention.

To solve this without impacting latency or adding load to PostgreSQL, GupShup uses **two dedicated atomic Redis algorithms**:
- **Chat Messages**: Sliding Window via Redis Sorted Set (`ZSET`)
- **Matchmaking /search**: Token Bucket via Redis Hash (`HASH`)

---

## 2. Algorithms & Redis Architecture

```
                    Incoming User Action
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
       Chat Message                        /search
            │                                 │
     Sliding Window                      Token Bucket
   rl:msg:{user_id}                   rl:search:{user_id}
   (Redis Sorted Set)                    (Redis Hash)
            │                                 │
   Atomic Lua Script                 Atomic Lua Script
   • Clean expired scores             • Refill tokens via delta
   • Count active entries             • Check tokens >= cost
   • Quota: 5 msgs / 2s               • Quota: 1 attempt / 3s
            │                                 │
      Allowed?                          Allowed?
     ├── YES ──► Relay to Partner       ├── YES ──► Enqueue into ZSET
     └── NO  ──► Prompt User & Backoff  └── NO  ──► Reply Backoff Card
```

### 2.1 Chat Messages: Atomic Sliding Window

- **Redis Key**: `rl:msg:{user_id}`
- **Data Structure**: Sorted Set (`ZSET`)
- **Score**: Unix epoch timestamp in milliseconds (`now_ms`)
- **Member**: Unique request identifier (`{now_ms}:{uuid}`)
- **Default Limit**: 5 messages per 2.0 seconds (`RATE_LIMIT_CHAT_MAX_MESSAGES=5`, `RATE_LIMIT_CHAT_WINDOW_SECONDS=2.0`)

#### Sliding Window Execution Flow (Lua)
1. **Prune**: Remove all entries older than `now_ms - window_ms` using `ZREMRANGEBYSCORE`.
2. **Count**: Retrieve current count in the active window via `ZCARD`.
3. **Evaluate**:
   - If `current_count < limit`:
     - Add current request to the ZSET via `ZADD`.
     - Refresh key TTL with `PEXPIRE` (set to $2 \times \text{window\_ms}$).
     - Return `{1, remaining, 0}`.
   - If `current_count >= limit`:
     - Query the score of the oldest entry in the current window via `ZRANGE 0 0 WITHSCORES`.
     - Calculate exact backoff duration:
       $$\text{retry\_after\_ms} = (\text{oldest\_timestamp} + \text{window\_ms}) - \text{now\_ms}$$
     - Refresh key TTL so successive checks don't lose the window boundary.
     - Return `{0, 0, retry_after_ms}`.

### 2.2 /search Command: Atomic Token Bucket

- **Redis Key**: `rl:search:{user_id}`
- **Data Structure**: Hash (`HASH`) with fields `tokens` and `last_updated`
- **Default Limit**: 1 attempt per 3.0 seconds (`RATE_LIMIT_SEARCH_CAPACITY=1`, `RATE_LIMIT_SEARCH_REFILL_PERIOD_SECONDS=3.0`)
- **Refill Rate**:
  $$r = \frac{1}{\text{refill\_period\_ms}} \text{ tokens/ms}$$

#### Token Bucket Execution Flow (Lua)
1. **Fetch & Initialize**: Fetch `tokens` and `last_updated` via `HMGET`. If empty, initialize with `tokens = capacity` and `last_updated = now_ms`.
2. **Refill**: Calculate elapsed time since `last_updated` and add refilled tokens:
   $$\text{tokens} = \min(\text{capacity}, \text{tokens} + \text{elapsed} \times r)$$
3. **Consume**:
   - If $\text{tokens} \ge 1.0$:
     - Deduct 1 token: $\text{tokens} = \text{tokens} - 1.0$.
     - Save updated state via `HMSET`.
     - Set TTL to $\max(2 \times \text{refill\_period}, 10\text{s})$ via `PEXPIRE`.
     - Return `{1, remaining, 0}`.
   - If $\text{tokens} < 1.0$:
     - Calculate wait time for the missing fraction:
       $$\text{needed} = 1.0 - \text{tokens}$$
       $$\text{retry\_after\_ms} = \max(1, \lceil\text{needed} \times \text{refill\_period\_ms}\rceil)$$
     - Save state and refresh TTL.
     - Return `{0, 0, retry_after_ms}`.

---

## 3. Architectural Guarantees & Edge Cases

| Guarantee | How It Is Achieved |
|:--|:--|
| **Zero PostgreSQL Hot-Path Load** | Evaluated 100% in-memory via Redis. PostgreSQL is never queried or locked during rate limit checks. |
| **Atomic Concurrency Safety** | Both algorithms are executed as atomic Redis Lua scripts (`SLIDING_WINDOW_LUA`, `TOKEN_BUCKET_LUA`). No Python `asyncio.Lock` is used, making it completely race-free across multiple bot instances and workers. |
| **Fail-Safe Degradation** | If Redis times out, disconnects, or restarts, the Python wrapper catches the exception and **fails open** (`allowed=True`, `remaining=1`, `retry_after=0.0`). The bot will not crash or block legitimate traffic during transient Redis downtime. |
| **No Memory Leaks / Key Expiration** | Every Lua call resets key TTL via `PEXPIRE`. Unused keys automatically expire from Redis memory (4 seconds for chat keys, 10 seconds for search keys), preventing unbounded key accumulation. |
| **Privacy & Zero Content Logging** | Structured logging records only metadata (`user_id`, `limit`, `remaining`, `retry_after`). Message text, media identifiers, and personal user data are strictly excluded from logs. |
| **Accurate Backoff Timing** | `retry_after` is calculated from actual elapsed timestamps, returning sub-second precision (e.g. `1.85s`) rather than coarse static intervals. |

---

## 4. Configuration Reference

All rate limit parameters can be customized via `.env` or application settings in [`app/config/settings.py`](file:///a:/telegram-matchmaker/app/config/settings.py):

| Setting | Environment Variable | Default | Description |
|:--|:--|:--|:--|
| `RATE_LIMIT_CHAT_MAX_MESSAGES` | `RATE_LIMIT_CHAT_MAX_MESSAGES` | `5` | Maximum messages allowed within the chat window |
| `RATE_LIMIT_CHAT_WINDOW_SECONDS` | `RATE_LIMIT_CHAT_WINDOW_SECONDS` | `2.0` | Sliding window duration in seconds |
| `RATE_LIMIT_SEARCH_CAPACITY` | `RATE_LIMIT_SEARCH_CAPACITY` | `1` | Token bucket burst capacity for `/search` |
| `RATE_LIMIT_SEARCH_REFILL_PERIOD_SECONDS` | `RATE_LIMIT_SEARCH_REFILL_PERIOD_SECONDS` | `3.0` | Refill duration (seconds per token) for `/search` |

---

## 5. Verification & Test Suite

The rate limiting engine is covered by an automated test suite in [`tests/test_rate_limiter.py`](file:///a:/telegram-matchmaker/tests/test_rate_limiter.py):

```bash
.\venv\Scripts\pytest -v tests/test_rate_limiter.py
```

### Verified Test Cases

1. **`test_sliding_window_normal_traffic`**: Verifies that 5 sequential messages within 2.0s are accepted with decreasing `remaining` quotas ($4, 3, 2, 1, 0$).
2. **`test_sliding_window_burst_rejection_and_retry_after`**: Verifies that the 6th message in a 2.0s burst is rejected and returns a positive `retry_after` ($\le 2.0\text{s}$).
3. **`test_sliding_window_window_progression`**: Verifies that after reaching the limit and waiting for the computed `retry_after` duration, the sliding window automatically admits new requests.
4. **`test_token_bucket_search_rate_limiting`**: Verifies that the first `/search` succeeds, an immediate repeated `/search` is rejected with `retry_after`, and searching after the refill period succeeds.
5. **`test_concurrent_requests_atomicity`**: Fires 20 concurrent chat message requests simultaneously (`asyncio.gather`) against a limit of 5. Verifies that **exactly 5 requests succeed and exactly 15 are rejected** with zero race conditions. Fires 10 concurrent search requests and verifies **exactly 1 succeeds and 9 are rejected**.
6. **`test_key_ttl_and_auto_expiration`**: Verifies that `rl:msg:{user_id}` and `rl:search:{user_id}` have active TTLs and do not persist indefinitely in Redis.
7. **`test_fail_safe_behavior_on_redis_error`**: Simulates Redis connection failures and verifies that the limiter fails open without raising exceptions or interrupting handlers.
