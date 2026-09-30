# Phase 7: Redis Connection & State Machine

## What This Phase Does

Phase 7 establishes the **high-speed volatile data infrastructure** and the **central runtime State Machine** for the matchmaker.

While PostgreSQL (Phases 3–6) stores durable, long-lived data (identities, profiles, search preferences), Redis is introduced to handle **ephemeral, sub-millisecond, highly concurrent state tracking**.

Phase 7 accomplishes three core goals:

1. **Redis Async Connection Pooling**: Sets up a managed connection pool with automated health checks, connection reuse, and clean application lifecycle hooks (startup verification and graceful shutdown).
2. **Distributed Concurrency Locking**: Provides distributed lock primitives to protect against race conditions (e.g., rapid button tapping, simultaneous matchmaking worker matches, and disconnect/re-queue collisions).
3. **Runtime User State Machine**: Defines and enforces the three mutual runtime states of every user (`IDLE`, `SEARCHING`, `CHATTING`), tracks their active partner and session identifiers, and guards against illegal transitions.
4. **State Injection Middleware**: Connects Redis state into the Aiogram bot pipeline, automatically equipping every incoming message or button tap with the user's real-time state.

---

## Why It's Built This Way

### Why Redis Instead of PostgreSQL for Matchmaking State?

A user's matchmaking state changes rapidly:
- An active user enters the queue (`IDLE` → `SEARCHING`).
- They get paired 4 seconds later (`SEARCHING` → `CHATTING`).
- They chat for two minutes, exchange 20 messages, and type `/next` (`CHATTING` → `SEARCHING`).
- They leave the queue (`SEARCHING` → `IDLE`).

Handling these volatile presence changes in PostgreSQL causes severe bottlenecks:
- **Disk I/O and WAL bloat**: Relational databases write every change to a Write-Ahead Log on disk. High-frequency state churn causes heavy disk write volume and table bloat.
- **Lock Contention**: When multiple concurrent workers check and update user states, database row locks create queueing and latency spikes.
- **Sub-millisecond Latency**: Redis keeps all data in memory, executing state reads and atomic transitions in sub-millisecond time (`< 1ms` vs `5-20ms` in relational SQL).

PostgreSQL remains the source of truth for **durable identity**, while Redis acts as the source of truth for **active presence and matchmaking**.

### Why Enforce a Strict State Transition Matrix?

In an anonymous matchmaker, allowing out-of-order actions creates catastrophic bugs:
- What if a user who is already chatting presses `/search`? They would be placed in the queue while still connected to their existing partner.
- What if a user who is idle sends a raw message? Without state validation, the system wouldn't know who to send it to.
- What if a user who is not searching suddenly transitions to chatting?

A formal State Machine enforces strict boundaries:
- Transitions can only follow explicitly permitted paths.
- Any illegal transition is immediately rejected and logged.
- The user's current state dictates which bot commands and message actions are valid.

### Why Distributed Locking?

In a multi-user, multi-worker system, concurrency race conditions are inevitable without distributed locking:

**Scenario: The Double-Match Race Condition**
1. User A is waiting in the queue.
2. Worker 1 finds a match between User A and User B.
3. Simultaneously, Worker 2 finds a match between User A and User C.
4. Without a lock, both workers might write `CHATTING` to User A's record, leaving User A connected to two partners at once, while User B and User C receive fragmented messages.

With Redis distributed locks:
- Any worker attempting to transition a user's state must acquire an exclusive lock for that user.
- Worker 1 acquires the lock, pairs User A with User B, and changes User A's state to `CHATTING`.
- Worker 2 fails to acquire the lock (or sees User A is no longer `SEARCHING`) and safely moves on to other candidates.

### Why Connection Pooling?

Opening a new TCP connection to Redis for every incoming Telegram message incurs significant overhead (TCP handshake, TLS negotiation, authentication). A connection pool maintains an established group of persistent connections that are borrowed and returned in microseconds, maximizing throughput under heavy chat loads.

---

## How It Works (Implementation Architecture)

### 1. The Three Runtime States

Every user in the system is in exactly one of three states at any given moment:

```
┌─────────────────────────────────────────────────────────────┐
│                            IDLE                             │
│  - Default state for all users.                             │
│  - Not in the queue, not in an active chat.                 │
│  - Can run /search, /edit, /settings, /help.                │
│  - Unsolicited text messages prompt them to search.         │
└──────────────┬──────────────────────────────▲───────────────┘
               │                              │
        User runs /search              User runs /stop
               │                      or partner leaves
               ▼                              │
┌─────────────────────────────┐        ┌──────┴───────────────┐
│          SEARCHING          │        │       CHATTING       │
│  - Waiting in Redis queue.  │        │  - Paired with       │
│  - Matchmaker candidate.    │───────►│    anonymous partner.│
│  - Can cancel with /stop.   │  Match │  - Messages routed.  │
│  - Commands like /edit      │  found │  - Can run /next or  │
│    are temporarily locked.  │        │    /stop /end.       │
└─────────────────────────────┘        └──────────────┬───────┘
               ▲                                      │
               └────────────── User runs /next ───────┘
                         (Skip directly to next partner)
```

### 2. State Transition Matrix

The table below outlines what transitions are valid and what triggers them:

| From State | To State | Trigger | Permitted? | Notes |
|---|---|---|:---:|---|
| `IDLE` | `SEARCHING` | `/search` command | ✅ Yes | User enters the matchmaking queue. |
| `IDLE` | `CHATTING` | Direct match | ❌ Prohibited | Users cannot be matched without entering the queue first. |
| `SEARCHING` | `IDLE` | `/stop` / timeout | ✅ Yes | User voluntarily leaves queue or queue times out. |
| `SEARCHING` | `CHATTING` | Match found | ✅ Yes | Matchmaking worker pairs user with a compatible partner. |
| `SEARCHING` | `SEARCHING` | Repeat `/search` | ❌ Prohibited | Re-entry ignored; prevents duplicate queue entries. |
| `CHATTING` | `IDLE` | `/stop`, `/end`, partner leaves | ✅ Yes | Active chat ends cleanly; both users return to IDLE. |
| `CHATTING` | `SEARCHING` | `/next` command | ✅ Yes | Ends current chat and immediately re-enters queue. |
| `CHATTING` | `CHATTING` | Repeat match | ❌ Prohibited | Already in a chat; cannot be matched again. |
| *ANY* | `IDLE` | Emergency reset (`force_idle`) | ✅ Yes | Used for error recovery, unhandled exceptions, or admin resets. |

### 3. Redis Data Structure

Each user's state is stored in a Redis Hash at the key:

`user:state:<telegram_id>`

Fields within the hash:

| Field | Type | Description |
|---|---|---|
| `state` | String | Current state value: `"IDLE"`, `"SEARCHING"`, or `"CHATTING"` |
| `partner_id` | String | Telegram ID of the connected chat partner (empty string if not chatting) |
| `session_id` | String | Unique UUID of the active conversation session (empty string if not chatting) |
| `entered_at` | Float | Unix timestamp when the user entered this state |
| `updated_at` | Float | Unix timestamp of the most recent state change or activity |

#### Why Hashes?
- **O(1) Full Record Retrieval**: Retrieving the complete state record takes a single `HGETALL` command.
- **O(1) Direct Key Query**: Querying only the partner ID takes a single `HGET` command without deserializing an entire JSON blob.
- **Atomic Field Updates**: Updating only the timestamp or session state can be done without overwriting unaffected fields.

### 4. Concurrency Protection with Distributed Locks

State transitions use a dedicated lock key:

`lock:state:<telegram_id>`

Whenever a transition is initiated:
1. The process attempts to acquire the lock with a brief blocking timeout.
2. If the lock is held by another worker, the request waits or fails gracefully, preventing race conditions.
3. Upon acquiring the lock, the process reads the current state, validates that the requested transition is in the allowed transition set, and writes the updated hash.
4. The lock is released in an isolated `finally` block, ensuring no deadlocks occur even if an unhandled error arises.

### 5. Bot Middleware Event Lifecycle

With the `UserStateMiddleware` registered on the Aiogram dispatcher:
1. An incoming update (e.g. text message or inline button callback) arrives from Telegram.
2. The middleware extracts the user's Telegram ID.
3. It fetches the user's current runtime state from Redis in sub-millisecond time.
4. It injects both `user_match_state` and `match_state_mgr` into the handler's execution context.
5. Downstream handlers can immediately check:
   - Is this user idle?
   - Are they in the middle of a chat?
   - Who is their chat partner?
   All without executing a single SQL query.

---

## What Comes Next

With Redis connection pooling, distributed locking, and the runtime State Machine fully in place, we are ready for **Phase 8: Matchmaking Queue**:
- Storing waiting users in the Redis sorted set / queue (`matchmaking:waiting`).
- Implementing the `/search` command to trigger queue entry and state transition to `SEARCHING`.
- Handling `/stop` or `/cancel` to safely remove users from the queue and return them to `IDLE`.
