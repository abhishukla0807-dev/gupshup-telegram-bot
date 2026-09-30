"""
Matchmaking Engine — Constraint filtering, weighted scoring, and atomic candidate claiming.

Optimized for ultra-low latency:
  - Bounded candidate discovery (BATCH_SIZE=100, single MGET)
  - 6-factor weighted compatibility scoring
  - Redis-backed O(1) block checks (no PostgreSQL in hot path)
  - Atomic Lua claim for concurrency safety
  - Pre-resolved user_uuid eliminates handshake SELECTs
"""
import logging
import time
import uuid
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis

from app.core.matching.queue_service import MatchmakingQueueService, QUEUE_KEY
from app.core.matching.schemas import QueueCandidate
from app.core.moderation.service import ModerationService
from app.core.sessions.service import SessionService
from app.infrastructure.redis import get_redis_client

logger = logging.getLogger(__name__)

# Atomic claim Lua script:
# Atomically verifies both candidates are waiting and not claimed,
# reserves claim keys with TTL, and removes both from the waiting ZSET.
CLAIM_PAIR_LUA = """
local user_a = KEYS[1]
local user_b = KEYS[2]
local worker_id = ARGV[1]
local ttl = tonumber(ARGV[2])

local score_a = redis.call("ZSCORE", "matchmaking:waiting", user_a)
local score_b = redis.call("ZSCORE", "matchmaking:waiting", user_b)
if not score_a or not score_b then
    return 0
end

if redis.call("EXISTS", "matchmaking:claimed:" .. user_a) == 1 or
   redis.call("EXISTS", "matchmaking:claimed:" .. user_b) == 1 then
    return 0
end

local state_a = redis.call("HGET", "user:state:" .. user_a, "state")
local state_b = redis.call("HGET", "user:state:" .. user_b, "state")
if state_a ~= "SEARCHING" or state_b ~= "SEARCHING" then
    return 0
end

redis.call("SET", "matchmaking:claimed:" .. user_a, worker_id, "EX", ttl)
redis.call("SET", "matchmaking:claimed:" .. user_b, worker_id, "EX", ttl)
redis.call("ZREM", "matchmaking:waiting", user_a, user_b)

return 1
"""

# Release claim Lua script (used if downstream handshake fails)
RELEASE_CLAIM_LUA = """
local user_a = KEYS[1]
local user_b = KEYS[2]
local worker_id = ARGV[1]
local re_enqueue = tonumber(ARGV[2])
local score_a = ARGV[3]
local score_b = ARGV[4]

if redis.call("GET", "matchmaking:claimed:" .. user_a) == worker_id then
    redis.call("DEL", "matchmaking:claimed:" .. user_a)
    if re_enqueue == 1 and score_a ~= "" then
        redis.call("ZADD", "matchmaking:waiting", score_a, user_a)
    end
end

if redis.call("GET", "matchmaking:claimed:" .. user_b) == worker_id then
    redis.call("DEL", "matchmaking:claimed:" .. user_b)
    if re_enqueue == 1 and score_b ~= "" then
        redis.call("ZADD", "matchmaking:waiting", score_b, user_b)
    end
end

return 1
"""


from app.config.settings import settings


class MatchmakingConfig:
    """Configurable weights for 6-factor candidate scoring and queue limits."""
    # --- Target 6-Factor Weights (100% total) ---
    WEIGHT_LANGUAGE: float = 0.30
    WEIGHT_INTERESTS: float = 0.25
    WEIGHT_LOCATION: float = 0.15
    WEIGHT_AGE: float = 0.15
    WEIGHT_MEDIA: float = 0.05
    WEIGHT_WAITING_TIME: float = 0.10

    MAX_WAIT_TIME_SECONDS: float = 120.0
    CLAIM_TTL_SECONDS: int = 15

    # Graceful degradation & queue limits
    HARD_CONSTRAINT_TIMEOUT_SECONDS: float = getattr(settings, "MATCHMAKING_HARD_CONSTRAINT_TIMEOUT_SECONDS", 10.0)
    MAX_QUEUE_WAIT_SECONDS: float = getattr(settings, "MATCHMAKING_MAX_QUEUE_WAIT_SECONDS", 60.0)


class MatchmakingEngine:
    """
    Evaluates waiting candidates, performs constraint checks,
    computes compatibility scores, and claims pairs atomically.

    Optimization targets:
      - Candidate discovery: O(1) RTT via bounded MGET
      - Block checks: O(1) via Redis SISMEMBER (no PostgreSQL)
      - Scoring: 6-factor weighted vectorized computation
      - Session handshake: 0 redundant SELECTs via pre-resolved user_uuid
    """

    def __init__(
        self,
        queue_service: MatchmakingQueueService | None = None,
        session_service: SessionService | None = None,
        moderation_service: ModerationService | None = None,
        redis: Redis | None = None,
        worker_id: str | None = None,
        config: MatchmakingConfig | None = None,
    ) -> None:
        self.queue_service = queue_service or MatchmakingQueueService()
        self.session_service = session_service or SessionService()
        self.moderation_service = moderation_service or ModerationService()
        self._redis = redis
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.config = config or MatchmakingConfig()

    def _get_client(self) -> Redis:
        return self._redis or get_redis_client()

    def check_hard_constraints(
        self,
        cand_a: QueueCandidate,
        cand_b: QueueCandidate,
    ) -> bool:
        """
        Check mutual hard constraints between candidate A and candidate B.
        Returns True if candidates are strictly eligible to be matched.

        Pure CPU check — no I/O calls.
        """
        # 1. Identity check
        if cand_a.telegram_id == cand_b.telegram_id:
            return False

        # 2. Mutual Gender
        if cand_a.preferred_gender != "any" and cand_a.preferred_gender != cand_b.gender:
            return False
        if cand_b.preferred_gender != "any" and cand_b.preferred_gender != cand_a.gender:
            return False

        # 3. Mutual Age
        if not (cand_a.preferred_age_min <= cand_b.age <= cand_a.preferred_age_max):
            return False
        if not (cand_b.preferred_age_min <= cand_a.age <= cand_b.preferred_age_max):
            return False

        # 4. Mutual Language
        if cand_a.preferred_language != "any" and cand_b.preferred_language != "any":
            if cand_a.preferred_language != cand_b.preferred_language:
                return False
        elif cand_a.preferred_language != "any":
            if cand_a.preferred_language != cand_b.language:
                return False
        elif cand_b.preferred_language != "any":
            if cand_b.preferred_language != cand_a.language:
                return False

        return True

    def calculate_score(
        self,
        cand_a: QueueCandidate,
        cand_b: QueueCandidate,
        now: float,
    ) -> float:
        """
        Compute 6-factor weighted compatibility score.
        Output is normalized to a [0.0, 1.0] scale.

        Factors:
          1. Language Affinity (30%)
          2. Interests Overlap (25%) — Jaccard similarity
          3. Location Proximity (15%)
          4. Age Fit (15%)
          5. Media Compatibility (5%)
          6. Waiting Time Priority (10%)
        """
        cfg = self.config

        # 1. Language Affinity (30%)
        if cand_a.language == cand_b.language:
            s_lang = 1.0
        elif cand_a.preferred_language == "any" or cand_b.preferred_language == "any":
            s_lang = 0.7
        else:
            s_lang = 0.1

        # 2. Interests Overlap (25%) — Jaccard similarity
        set_a = set(cand_a.interests) if cand_a.interests else set()
        set_b = set(cand_b.interests) if cand_b.interests else set()
        if set_a or set_b:
            union_size = len(set_a | set_b)
            s_interest = len(set_a & set_b) / max(1, union_size)
        else:
            # Neither has interests specified — neutral baseline
            s_interest = 0.5

        # 3. Location Proximity (15%)
        loc_a = cand_a.location.strip().lower() if cand_a.location else ""
        loc_b = cand_b.location.strip().lower() if cand_b.location else ""
        if loc_a and loc_b and loc_a == loc_b:
            s_loc = 1.0
        elif not loc_a or not loc_b:
            s_loc = 0.5  # Unspecified = neutral
        else:
            s_loc = 0.2  # Different specific locations

        # 4. Age Fit (15%)
        age_diff = abs(cand_a.age - cand_b.age)
        s_age = max(0.0, 1.0 - (age_diff / 20.0))

        # 5. Media Compatibility (5%)
        s_media = 1.0 if cand_a.media_enabled == cand_b.media_enabled else 0.3

        # 6. Waiting Time Priority (10%)
        oldest_joined = min(cand_a.joined_at, cand_b.joined_at)
        wait_seconds = max(0.0, now - oldest_joined)
        s_wait = min(1.0, wait_seconds / cfg.MAX_WAIT_TIME_SECONDS)

        # Weighted aggregate
        total = (
            s_lang * cfg.WEIGHT_LANGUAGE
            + s_interest * cfg.WEIGHT_INTERESTS
            + s_loc * cfg.WEIGHT_LOCATION
            + s_age * cfg.WEIGHT_AGE
            + s_media * cfg.WEIGHT_MEDIA
            + s_wait * cfg.WEIGHT_WAITING_TIME
        )
        return total

    async def _check_block_fast(self, tg_id_a: int, tg_id_b: int) -> bool:
        """
        Fast Redis-backed mutual block check.
        Returns True if either user has blocked the other.
        """
        return await self.moderation_service.are_users_mutually_blocked_fast(tg_id_a, tg_id_b)

    async def attempt_claim_pair(
        self,
        cand_a: QueueCandidate,
        cand_b: QueueCandidate,
    ) -> bool:
        """Atomically claim two candidates via Redis Lua script."""
        client = self._get_client()
        keys = [str(cand_a.telegram_id), str(cand_b.telegram_id)]
        args = [self.worker_id, str(self.config.CLAIM_TTL_SECONDS)]

        result = await client.eval(CLAIM_PAIR_LUA, len(keys), *keys, *args)
        return bool(result == 1)

    async def release_claim_pair(
        self,
        cand_a: QueueCandidate,
        cand_b: QueueCandidate,
        re_enqueue: bool = True,
    ) -> None:
        """Release atomic claims in Redis, optionally re-enqueuing candidates."""
        client = self._get_client()
        keys = [str(cand_a.telegram_id), str(cand_b.telegram_id)]
        args = [
            self.worker_id,
            "1" if re_enqueue else "0",
            str(cand_a.joined_at),
            str(cand_b.joined_at),
        ]
        await client.eval(RELEASE_CLAIM_LUA, len(keys), *keys, *args)

    async def match_candidates(self) -> List[Tuple[QueueCandidate, QueueCandidate, str]]:
        """
        Execute one full matchmaking evaluation pass over waiting candidates.

        Optimized flow:
          1. Bounded MGET retrieval (100 oldest candidates, 1 RTT)
          2. In-memory hard constraints (pure CPU, zero I/O)
          3. Redis SISMEMBER block checks (O(1) per pair)
          4. 6-factor weighted scoring
          5. Atomic Lua claim
          6. PostgreSQL durable handshake (with pre-resolved UUIDs)

        Returns a list of successfully paired tuples:
          [(candidate_a, candidate_b, session_id), ...]
        """
        t_start = time.monotonic()

        # 1. Bounded candidate retrieval via pipelined MGET
        candidates = await self.queue_service.get_waiting_candidates_batched()
        if len(candidates) < 2:
            return []

        now = time.time()
        matched_pairs: List[Tuple[QueueCandidate, QueueCandidate, str]] = []
        claimed_in_this_pass: Set[int] = set()

        t_fetch = time.monotonic()

        for i, cand_a in enumerate(candidates):
            id_a = cand_a.telegram_id
            if id_a in claimed_in_this_pass:
                continue

            wait_a = max(0.0, now - cand_a.joined_at)
            a_relaxed = wait_a >= self.config.HARD_CONSTRAINT_TIMEOUT_SECONDS

            best_strict_partner: Optional[QueueCandidate] = None
            highest_strict_score: float = -1.0

            best_relaxed_partner: Optional[QueueCandidate] = None
            highest_relaxed_score: float = -1.0

            # Find best match among subsequent candidates
            for cand_b in candidates[i + 1:]:
                id_b = cand_b.telegram_id
                if id_b in claimed_in_this_pass:
                    continue

                if id_a == id_b:
                    continue

                # Redis block check (O(1) SISMEMBER, no PostgreSQL)
                # CRITICAL: Mutual blocks are NEVER relaxed
                if await self._check_block_fast(id_a, id_b):
                    continue

                # 6-factor weighted score calculation
                score = self.calculate_score(cand_a, cand_b, now)

                # 1. First priority: Strict hard constraints (pure CPU, no I/O)
                if self.check_hard_constraints(cand_a, cand_b):
                    if score > highest_strict_score:
                        highest_strict_score = score
                        best_strict_partner = cand_b
                else:
                    # 2. Second priority: If hard constraints don't match,
                    # after 10 seconds relaxation window, fall back to weighted scoring
                    wait_b = max(0.0, now - cand_b.joined_at)
                    b_relaxed = wait_b >= self.config.HARD_CONSTRAINT_TIMEOUT_SECONDS
                    if a_relaxed or b_relaxed:
                        if score > highest_relaxed_score:
                            highest_relaxed_score = score
                            best_relaxed_partner = cand_b

            # Resolution order: Hard constraints -> Weighted scoring -> Best candidate
            if best_strict_partner is not None:
                best_partner = best_strict_partner
                highest_score = highest_strict_score
                match_mode = "STRICT"
            elif best_relaxed_partner is not None:
                best_partner = best_relaxed_partner
                highest_score = highest_relaxed_score
                match_mode = "RELAXED_SCORING"
            else:
                best_partner = None
                highest_score = -1.0
                match_mode = "NONE"

            # If a compatible partner was found, attempt atomic claim & handshake
            if best_partner is not None:
                id_b = best_partner.telegram_id
                claimed = await self.attempt_claim_pair(cand_a, best_partner)
                if not claimed:
                    # Race condition: candidate was claimed by another worker or left
                    continue

                claimed_in_this_pass.add(id_a)
                claimed_in_this_pass.add(id_b)

                # Two-Phase Handshake: Create durable session in PostgreSQL
                # Use pre-resolved UUIDs to skip redundant SELECT queries
                user1_uuid = cand_a.user_uuid or None
                user2_uuid = best_partner.user_uuid or None
                chat_session, err = await self.session_service.create_chat_session(
                    id_a, id_b,
                    user1_uuid=user1_uuid,
                    user2_uuid=user2_uuid,
                )
                if chat_session is None:
                    logger.warning(
                        "PostgreSQL handshake failed for pair (%s, %s): %s. Re-enqueuing.",
                        id_a,
                        id_b,
                        err,
                    )
                    await self.release_claim_pair(cand_a, best_partner, re_enqueue=True)
                    claimed_in_this_pass.discard(id_a)
                    claimed_in_this_pass.discard(id_b)
                    continue

                # Clean up Redis claims & cached candidate data
                client = self._get_client()
                await client.delete(
                    f"matchmaking:claimed:{id_a}",
                    f"matchmaking:claimed:{id_b}",
                    f"matchmaking:candidate:{id_a}",
                    f"matchmaking:candidate:{id_b}",
                )

                sess_id_str = str(chat_session.id)
                logger.info(
                    "MATCH CREATED (%s): (%s <-> %s) score=%.3f session=%s by %s",
                    match_mode,
                    id_a,
                    id_b,
                    highest_score,
                    sess_id_str,
                    self.worker_id,
                )
                matched_pairs.append((cand_a, best_partner, sess_id_str))

        t_end = time.monotonic()
        if matched_pairs:
            logger.info(
                "Match pass completed: %d pairs in %.1fms (fetch=%.1fms, eval+claim=%.1fms)",
                len(matched_pairs),
                (t_end - t_start) * 1000,
                (t_fetch - t_start) * 1000,
                (t_end - t_fetch) * 1000,
            )

        return matched_pairs
