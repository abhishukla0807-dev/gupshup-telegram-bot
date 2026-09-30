# Phase 8: Matchmaking Queue

## What This Phase Does

Phase 8 implements the **waiting room and intake engine** for the anonymous matchmaker.

It bridges the user-facing bot commands (`/search`, `/stop`, `/cancel`) with the high-performance Redis data layer, providing a reliable, fair, and atomic queue where users wait until the matchmaking algorithm (Phase 9) pairs them with a compatible partner.

Specifically, Phase 8 delivers:

1. **Queue Management Service**: Handles atomic entry and exit from the matchmaking pool, tracks real-time queue lengths, and reports dynamic 1-based queue positions to waiting users.
2. **Candidate Metadata Caching**: Serializes and caches essential matching attributes (gender, age, language, and search preferences) into Redis upon queue entry, eliminating database query overhead during matching.
3. **Queue Bot Handlers (`/search`, `/stop`, `/cancel`)**: Equips users with intuitive entry into the matchmaking queue, visual queue progress feedback, and instant cancellation options (via command or inline button).
4. **State Machine Synchronization**: Coordinates with the Redis State Machine from Phase 7 to guarantee that enqueued users transition cleanly to `SEARCHING` and dequeued users return to `IDLE`.

---

## Why It's Built This Way

### Why Redis Sorted Sets (`ZSET`) for the Matchmaking Queue?

A matchmaking queue requires three distinct operational capabilities:
- **Strict FIFO Fairness**: Users who have waited longer must be evaluated first.
- **Fast Arbitrary Deletion**: When a user runs `/stop` or cancels via button, they must be removed from anywhere in the queue in `O(log N)` time.
- **Sub-millisecond Rank & Depth Queries**: Reporting *"You are #4 in queue"* must not require full array scans.

Standard Redis Lists (`LPUSH` / `RPOP`) fail because removing an arbitrary member from the middle of a list is an expensive `O(N)` scan.

A Redis Sorted Set (`ZSET`) solves all three requirements:
- The **Score** is the epoch timestamp (`time.time()`) when the user joined the queue. Sorting by score guarantees strict First-In, First-Out (FIFO) prioritization.
- `ZREM` removes any member instantly by identifier in `O(log N)` time.
- `ZRANK` yields the exact 0-based index of a user in `O(log N)` time, providing instant position feedback.

### Why Cache Candidate Metadata in Redis on Queue Entry?

In Phase 9, a background worker periodically evaluates candidates in the queue to find mutual matches:
- If 50 users are waiting in the queue, comparing them requires checking profile attributes and preference filters for each candidate.
- If this metadata had to be fetched from PostgreSQL on every evaluation cycle, the database would be bombarded with hundreds of relational queries per second.

By serializing the user's demographic profile and matching preferences into a Redis key (`matchmaking:candidate:<telegram_id>`) at the moment of `/search`, the matching engine can evaluate candidate compatibility purely in memory in sub-millisecond time with **zero load on PostgreSQL**.

### Why Integrate Distributed Locking with Queue Entry?

Tapping the `/search` button repeatedly or sending multiple concurrent commands could lead to:
- Duplicate entries in the queue.
- Conflicting state transitions.
- Corrupted candidate cache records.

By wrapping queue operations in a distributed lock (`lock:queue:<telegram_id>`), concurrent operations for the same user serialize cleanly. If a user double-taps `/search`, the second request is recognized as already in the queue and rejected gracefully without duplicate records.

### Why Inline Cancel Buttons Alongside `/stop`?

Mobile chat UX demands minimal friction:
- While power users may type `/stop` or `/cancel`, casual users prefer tapping a visible button.
- Providing an inline `❌ Cancel Search` button under the queue status message ensures users can leave the queue with a single tap, reducing ghost entries and stale waiting states.

---

## How It Works (Implementation Architecture)

### 1. High-Level Queue Lifecycle

```
                      [User Types /search]
                                │
                                ▼
                   [Eligibility Verification]
                   ├── Profile complete?
                   ├── Not banned?
                   └── Current state is IDLE?
                                │
               ┌────────────────┴────────────────┐
               │ No                              │ Yes
               ▼                                 ▼
      [Display Alert & Reject]          [Acquire Queue Lock]
                                                 │
                                                 ▼
                                        [Cache Candidate Data]
                                        (matchmaking:candidate:<id>)
                                                 │
                                                 ▼
                                         [Add to Redis ZSET]
                                         (matchmaking:waiting)
                                                 │
                                                 ▼
                                      [Transition to SEARCHING]
                                                 │
                                                 ▼
                                        [Release Queue Lock]
                                                 │
                                                 ▼
                                        [Send Queue Message]
                                   "Searching... Position #1"
```

### 2. Dequeue & Cancellation Lifecycle

```
            [User Types /stop or Taps "Cancel Search"]
                                │
                                ▼
                       [Acquire Queue Lock]
                                │
                                ▼
                       [Remove from ZSET]
                     (matchmaking:waiting)
                                │
                                ▼
                    [Delete Candidate Cache]
                  (matchmaking:candidate:<id>)
                                │
                                ▼
                      [Transition to IDLE]
                                │
                                ▼
                       [Release Queue Lock]
                                │
                                ▼
                     [Send Cancelled Notice]
                    "Search cancelled. Ready."
```

### 3. Redis Data Structures

The queue utilizes two coupled Redis structures:

#### A. Waiting Pool (`matchmaking:waiting`)
- **Data Type**: Sorted Set (`ZSET`)
- **Key**: `matchmaking:waiting`
- **Member**: User's Telegram ID (`string`)
- **Score**: Timestamp of entry (float epoch, e.g. `1727701234.56`)
- **Ordering**: Ascending score (oldest waiting user has rank 0)

#### B. Candidate Metadata Cache (`matchmaking:candidate:<telegram_id>`)
- **Data Type**: String (JSON-serialized payload)
- **Key**: `matchmaking:candidate:<telegram_id>`
- **TTL**: 3600 seconds (1 hour automatic expiry)
- **Payload Attributes**:
  - `telegram_id`: Unique identifier
  - `gender`: User's self-reported gender
  - `age`: User's age bracket midpoint
  - `language`: User's spoken conversation language
  - `preferred_gender`: Preferred partner gender (`"any"`, `"male"`, `"female"`, `"other"`)
  - `preferred_age_min` & `preferred_age_max`: Target partner age bracket
  - `preferred_language`: Target partner conversation language
  - `joined_at`: Timestamp of queue entry

### 4. Mutual Compatibility Validation Rules

For two candidates, User A and User B, to be considered a valid match by the matching logic, **both** criteria must be met simultaneously:

1. **User A satisfies User B's search preferences:**
   - User A's gender matches User B's `preferred_gender` (or User B selected `"any"`).
   - User A's age falls within User B's `[preferred_age_min, preferred_age_max]`.
   - User A's language matches User B's `preferred_language` (or User B selected `"any"`).

2. **User B satisfies User A's search preferences:**
   - User B's gender matches User A's `preferred_gender` (or User A selected `"any"`).
   - User B's age falls within User A's `[preferred_age_min, preferred_age_max]`.
   - User B's language matches User A's `preferred_language` (or User A selected `"any"`).

If any single condition fails in either direction, the candidate pair is rejected and the engine moves on to the next pair.

### 5. Bot Commands & UI Summary

| Command / Event | Permitted State | Resulting Action |
|---|---|---|
| `/search` | `IDLE` | Enqueues user, transitions state to `SEARCHING`, shows position & cancel button. |
| `/search` | `SEARCHING` | Ignores re-entry, informs user they are already in the queue, shows position. |
| `/search` | `CHATTING` | Rejects entry, instructs user to finish current chat via `/next` or `/end`. |
| `/stop` / `/cancel` | `SEARCHING` | Removes user from queue, transitions state to `IDLE`, confirms cancellation. |
| `/stop` / `/cancel` | `CHATTING` | Informs user to use `/end` to terminate active chat session. |
| `/stop` / `/cancel` | `IDLE` | Informs user they are not currently in the matchmaking queue. |
| Tap `❌ Cancel Search` | `SEARCHING` | Dequeues user, returns to `IDLE`, edits message to confirm cancellation. |

---

## What Comes Next

With the matchmaking queue and intake flow operational, we are ready for **Phase 9: Matchmaking Engine**:
- A background worker loop that periodically inspects `matchmaking:waiting`.
- Evaluating mutual compatibility across waiting candidates in FIFO order.
- Pairing compatible users atomically, removing them from the queue, generating an active session identifier, and transitioning both users from `SEARCHING` to `CHATTING`.
