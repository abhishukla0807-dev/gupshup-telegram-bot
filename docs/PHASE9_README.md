# Phase 9: Matchmaking Engine & Real-Time Anonymous Routing

## What This Phase Does

Phase 9 represents the core intelligence and concurrency-safe matching backbone of the **GupShup Anonymous Telegram Matchmaker**.

It transitions waiting candidates from the intake queue (Phase 8) into durable, private, two-way conversation sessions (Phase 10 & 11) while guaranteeing high-speed performance, absolute anonymity, and strict distributed consistency.

Key capabilities delivered in this phase:

1. **Two-Stage Matchmaking Pipeline**:
   - **Mutual Hard Constraints Filtering**: Evaluates age boundaries, gender preferences, spoken languages, and persistent blocklists in both directions before considering a match.
   - **Weighted Compatibility Scoring**: Ranks remaining valid pairs using a multi-factor formula that balances linguistic affinity, age proximity, preference precision, and waiting-time priority.

2. **Atomic Candidate Claiming (Redis Lua)**:
   - Prevents worker contention and double-matching using an atomic script. When two candidates are claimed, they are instantaneously locked and removed from the waiting pool in a single Redis instruction.

3. **Durable Handshake & Invariant Enforcement (PostgreSQL)**:
   - Enforces the invariant that **no user can ever have more than one active chat session** using a dedicated database exclusion table (`user_active_sessions`).

4. **Anonymous Message Relay & Recipient Derivation**:
   - Forwards text, photos, voice notes, stickers, and media between paired partners without ever allowing client input to designate the destination. The recipient is derived strictly from the active session.

5. **Self-Healing Reconciliation Janitor**:
   - A background auditing loop that detects stranded claims, re-hydrates volatile Redis presence from PostgreSQL ground truth during crashes, and prunes stale queue members.

6. **Full Session Controls**:
   - Commands `/next` (switch partner and re-queue), `/end` (leave conversation), and `/block` (terminate chat and record bidirectional block).

---

## Why It's Built This Way

### Why a Two-Phase Claim Handshake (Redis Lua + PostgreSQL Transaction)?

In a distributed environment where multiple workers or bot instances run concurrently:
- Redis provides **sub-millisecond evaluation and claiming** (`< 1ms`).
- PostgreSQL provides **durable persistence and relational constraints** (`ACID`).

If a worker directly attempted to create database sessions without a fast reservation in Redis, multiple workers would repeatedly hit PostgreSQL with optimistic locking conflicts. 

By executing an atomic Lua script in Redis first:
1. Two candidates are reserved with an ephemeral claim lock (`matchmaking:claimed:<id>`) and removed from the waiting set in one atomic step.
2. The worker proceeds to execute the PostgreSQL transaction.
3. If PostgreSQL commits successfully, Redis presence transitions to `CHATTING` and claim locks are released.
4. If PostgreSQL fails or times out, the claim locks expire or are released, and candidates are safely re-enqueued to preserve their original FIFO priority.

### Why an Explicit `user_active_sessions` Exclusion Table?

A chat session involves two foreign keys: `user1_id` and `user2_id`. Enforcing that a user cannot appear more than once across both columns in standard SQL requires complex triggers or dual conditional unique indexes.

By creating a dedicated table where `user_id` is the **PRIMARY KEY** referencing the active session:
- A user can only ever have **at most one row** in `user_active_sessions`.
- Any attempt to pair a user who is already in another conversation immediately triggers a database-level Unique Violation error, rolling back the transaction.
- When a session terminates, deleting the session record cascades or cleans up the user rows in the same transaction.

### Why Combine Weighted Compatibility with Waiting-Time Priority?

Pure greedy matching by compatibility score can cause "queue starvation" for users with niche preferences or minority demographic attributes. 

By blending:
- **Language Affinity** (highest conversational impact)
- **Age Proximity** (demographic alignment)
- **Preference Precision** (rewarding specific criteria)
- **Waiting-Time Priority** (dynamically scaling from 0 to 1 as wait time grows)

Users who have waited longer naturally accumulate priority points, widening their match threshold and ensuring fair queue progression for all participants.

### Why Derive Message Recipients Exclusively from the Active Session?

Allowing a client request or incoming message update to specify a `recipient_id` creates an immense security vulnerability:
- A malicious client could forge payloads to message arbitrary Telegram users.
- A user could bypass blocks or eavesdrop on private dialogs.

In GupShup, the message routing pipeline never reads a destination identifier from the client. It inspects the sender's authenticated Telegram ID, queries the verified active session from Redis/PostgreSQL, and routes the message to the corresponding partner.

---

## How It Works (Implementation Architecture)

### 1. Matchmaking Lifecycle Architecture

```
┌────────────────────────────────────────────────────────┐
│               Redis Intake Queue (ZSET)                │
│         [Candidate A]  [Candidate B]  [Candidate C]    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                   Matchmaking Engine                   │
│  Step 1: Hard Constraints Filter                       │
│          - Mutual gender & age compatibility           │
│          - Spoken language alignment                   │
│          - Bidirectional user block check              │
│                                                        │
│  Step 2: Weighted Compatibility Scoring                │
│          - Language + Age + Specificity + Wait Time    │
│          - Selects highest scoring pair (A, B)         │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│              Phase 1: Atomic Redis Claim               │
│  - Execute Lua script: claim_pair                      │
│  - Verifies both candidates still waiting              │
│  - Sets matchmaking:claimed:A and claimed:B            │
│  - Atomically removes A and B from waiting queue       │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│            Phase 2: PostgreSQL Durable Commit          │
│  - Begin transaction                                   │
│  - Insert ChatSession (status = 'active')              │
│  - Insert UserActiveSession (user1_id, session_id)     │
│  - Insert UserActiveSession (user2_id, session_id)     │
│  - Commit (Enforces exactly 1 active session per user) │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│               Phase 3: Redis State Activation          │
│  - Set user:state:A -> CHATTING (partner=B, session)   │
│  - Set user:state:B -> CHATTING (partner=A, session)   │
│  - Cache session:active:<session_id> routing hash      │
│  - Release temporary claim locks                       │
│  - Dispatch "Partner Found!" messages via Telegram     │
└────────────────────────────────────────────────────────┘
```

### 2. Candidate Matching Matrix

| Filter Criterion | Validation Rule | Impact on Failure |
|---|---|:---:|
| **Identity Check** | `Candidate A != Candidate B` | Discarded |
| **Mutual Block Check** | `A not in B's blocks` AND `B not in A's blocks` | Discarded |
| **Mutual Gender** | `A.gender matches B.pref` AND `B.gender matches A.pref` | Discarded |
| **Mutual Age** | `A.age in B's range` AND `B.age in A's range` | Discarded |
| **Mutual Language** | `A.lang matches B.pref` AND `B.lang matches A.pref` | Discarded |
| **Active Availability** | Both candidates confirmed in `SEARCHING` state | Discarded |

### 3. Session Controls & Teardown Dynamics

#### `/next` (Switch to Next Partner)
1. Sender triggers `/next`.
2. Active session is terminated in PostgreSQL with reason `"next"`.
3. The partner is notified that their partner moved on.
4. The initiator is automatically re-enqueued into `matchmaking:waiting` with a fresh score.
5. The abandoned partner returns to `IDLE` with prompt to `/search`.

#### `/end` (Disconnect Conversation)
1. Sender triggers `/end`.
2. Active session is terminated in PostgreSQL with reason `"user_left"`.
3. Active session records are removed from `user_active_sessions`.
4. Both participants are transitioned to `IDLE` state in Redis.
5. Farewell messages are delivered to both parties.

#### `/block` (Report & Permanent Separation)
1. Sender triggers `/block`.
2. A new record is inserted into `user_blocks` (with unique pair constraint).
3. The active session is closed with reason `"blocked"`.
4. Both participants are transitioned to `IDLE`.
5. The matchmaking engine will never evaluate or pair these two users again.

### 4. Self-Healing Reconciliation Janitor

The Janitor loop runs periodically in the background to ensure absolute consistency:

- **Expired Claim Recovery**: Identifies candidates whose claim keys expired due to an unhandled worker process crash and re-integrates them into the waiting queue.
- **Lost State Rehydration**: Scans `user_active_sessions` in PostgreSQL. If a user is active in the database but Redis lost state due to eviction or restart, the Janitor immediately re-hydrates Redis presence to `CHATTING`.
- **Ghost State Clearing**: If a user's Redis state shows `CHATTING` but no active session exists in PostgreSQL, the Janitor resets the user to `IDLE`.

---

## Concurrency Test Validation Summary

The complete engine was subjected to concurrency tests:

- **Constraint Verification**: 100% of incompatible demographic and blocked pairs rejected.
- **PostgreSQL Invariant**: Concurrent attempt to insert a second active session for a user failed with `UniqueViolation` and rolled back.
- **Worker Contention**: Two workers competing for the same candidate resolved with exactly one winner via atomic Lua script.
- **60-User Parallel Stress Test**: 60 concurrent users enqueued simultaneously and matched into 30 distinct pairs with zero collisions, zero duplicates, and exactly 60 active database records.
- **Race Resolution**: Concurrent `/next` vs `/end` calls resolved idempotently without duplicate state corruptions.
- **State Recovery**: Redis crash simulation successfully rehydrated from PostgreSQL ground truth.

---

## What Comes Next

With the matchmaking engine, atomic claim pipeline, and anonymous message relay fully operational:
- Matching, chat sessions, and moderation blocking are functional end-to-end.
- We are ready for **Phase 12: Inactivity Timeouts & Media Moderation**, ensuring inactive chat rooms are pruned and photo/media exchange complies with community standards.
