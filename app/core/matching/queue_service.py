"""
Matchmaking Queue Service — manages the Redis waiting queue and candidate pooling.

Redis Data Structures:
  - ZSET `matchmaking:waiting`
      Member: telegram_id (str)
      Score: epoch timestamp (float) for strict FIFO queuing
  - STRING `matchmaking:candidate:{telegram_id}`
      Value: JSON serialized QueueCandidate metadata
      TTL: 3600s (auto-expire stale entries)
"""
import json
import logging
import time
import uuid
from typing import List, Optional, Tuple

from redis.asyncio import Redis

from app.core.matching.schemas import QueueCandidate
from app.core.sessions.state_machine import UserMatchState, UserStateData, UserStateManager
from app.core.users.preferences_service import PreferencesService
from app.core.users.profile_service import ProfileService
from app.core.users.service import UserService
from app.infrastructure.redis import get_redis_client, redis_lock

logger = logging.getLogger(__name__)

QUEUE_KEY = "matchmaking:waiting"
CANDIDATE_PREFIX = "matchmaking:candidate:"
CANDIDATE_TTL = 3600  # 1 hour queue TTL
BATCH_SIZE = 100  # Maximum candidates fetched per worker tick


class MatchmakingQueueService:
    """Encapsulates all queue operations for waiting matchmaking candidates."""

    def __init__(
        self,
        redis: Redis | None = None,
        state_manager: UserStateManager | None = None,
        profile_service: ProfileService | None = None,
        preferences_service: PreferencesService | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self._redis = redis
        self.state_manager = state_manager or UserStateManager()
        self.profile_service = profile_service or ProfileService()
        self.preferences_service = preferences_service or PreferencesService()
        self.user_service = user_service or UserService()

    def _get_client(self) -> Redis:
        return self._redis or get_redis_client()

    @staticmethod
    def _candidate_key(telegram_id: int) -> str:
        return f"{CANDIDATE_PREFIX}{telegram_id}"

    async def _is_session_genuinely_active(
        self,
        telegram_id: int,
        state: UserStateData,
    ) -> bool:
        """
        Verify if a user's CHATTING state corresponds to a truly alive, bidirectional session.

        Returns False if:
          1. Missing partner_id or session_id metadata.
          2. The partner is no longer in CHATTING state with this user.
          3. The active session routing cache is missing from Redis AND the session is ended/missing in PostgreSQL.
        """
        if not state.session_id or not state.partner_id:
            return False

        # Check partner's state in Redis (bidirectional invariant)
        partner_state = await self.state_manager.get_state(state.partner_id)
        if (
            partner_state.state != UserMatchState.CHATTING
            or partner_state.partner_id != telegram_id
            or partner_state.session_id != state.session_id
        ):
            logger.debug(
                "Partner %s not mutually chatting with %s (partner_state=%s, partner_partner=%s)",
                state.partner_id,
                telegram_id,
                partner_state.state.value,
                partner_state.partner_id,
            )
            return False

        # Check Redis active session routing cache
        client = self._get_client()
        active_cache = await client.hgetall(f"session:active:{state.session_id}")
        if active_cache and active_cache.get("status") == "active":
            return True

        # Fallback to PostgreSQL active session verification
        try:
            from app.infrastructure.database import AsyncSessionLocal
            from app.core.sessions.repository import SessionRepository
            sess_uuid = uuid.UUID(state.session_id)
            async with AsyncSessionLocal() as db_session:
                repo = SessionRepository(db_session)
                db_sess = await repo.get_by_id(sess_uuid)
                if db_sess is None or db_sess.status != "active":
                    return False
                return True
        except Exception:
            return False

    async def enqueue(self, telegram_id: int) -> Tuple[bool, str, int]:
        """
        Add a user to the matchmaking queue.

        Returns (success: bool, message: str, queue_position: int).
        """
        async with redis_lock(f"queue:{telegram_id}", timeout=5.0):
            # 1. Verify user state with auto-healing for stale/zombie sessions
            current_state = await self.state_manager.get_state(telegram_id)
            if current_state.is_chatting:
                is_active = await self._is_session_genuinely_active(telegram_id, current_state)
                if is_active:
                    return False, "You are currently in an active chat.", 0
                else:
                    logger.warning(
                        "Auto-healing zombie CHATTING state for user %s (session=%s, partner=%s)",
                        telegram_id,
                        current_state.session_id,
                        current_state.partner_id,
                    )
                    await self.state_manager.force_idle(telegram_id)
                    current_state = await self.state_manager.get_state(telegram_id)

            if current_state.is_searching:
                pos = await self.get_queue_position(telegram_id) or 1
                return False, "You are already in the matchmaking queue.", pos

            # 2. Verify complete profile
            has_profile = await self.profile_service.has_complete_profile(telegram_id)
            if not has_profile:
                return (
                    False,
                    "Please complete your profile first using /start.",
                    0,
                )

            # 3. Fetch profile & preferences from DB
            profile = await self.profile_service.get_profile(telegram_id)
            if profile is None:
                return False, "Profile could not be loaded.", 0

            prefs = await self.preferences_service.get_preferences(telegram_id)
            if prefs is None or not prefs.is_active:
                return False, "Your search preferences are inactive or not found.", 0

            # 3b. Resolve user UUID for session handshake optimization
            user, _ = await self.user_service.register_or_fetch(
                telegram_id=telegram_id,
                first_name="",  # Won't create since user already exists
            )
            if user and user.is_banned:
                return False, "Your account has been suspended from matchmaking.", 0

            user_uuid_str = str(user.id) if user else ""

            # 3c. Fetch interests and location from profile (defaults if not available)
            interests: list[str] = []
            location: str = ""
            media_enabled: bool = True
            if hasattr(profile, "interests") and profile.interests:
                interests = profile.interests if isinstance(profile.interests, list) else []
            if hasattr(profile, "location") and profile.location:
                location = profile.location
            if hasattr(profile, "media_enabled"):
                media_enabled = bool(profile.media_enabled)

            # 4. Build candidate payload with all 6-factor scoring fields
            now = time.time()
            candidate = QueueCandidate(
                telegram_id=telegram_id,
                user_uuid=user_uuid_str,
                gender=profile.gender,
                age=profile.age,
                language=profile.language,
                interests=interests,
                location=location,
                media_enabled=media_enabled,
                preferred_gender=prefs.preferred_gender,
                preferred_age_min=prefs.preferred_age_min,
                preferred_age_max=prefs.preferred_age_max,
                preferred_language=prefs.preferred_language,
                joined_at=now,
            )

            # 5. Store in Redis (single pipeline for atomicity)
            client = self._get_client()
            pipe = client.pipeline(transaction=False)
            cand_key = self._candidate_key(telegram_id)
            pipe.set(cand_key, candidate.model_dump_json(), ex=CANDIDATE_TTL)
            pipe.zadd(QUEUE_KEY, {str(telegram_id): now})
            await pipe.execute()

            # 6. Sync blocklist to Redis for fast in-memory checks during matching
            await self._sync_blocklist_to_redis(telegram_id)

            # 7. Update user state to SEARCHING
            await self.state_manager.transition_to(
                telegram_id=telegram_id,
                target_state=UserMatchState.SEARCHING,
                use_lock=False,  # Already holding queue lock
            )

            # 8. Get position in queue
            rank = await client.zrank(QUEUE_KEY, str(telegram_id))
            position = (rank + 1) if rank is not None else 1

            logger.info(
                "User %s enqueued at position %s (gender=%s, age=%s, lang=%s, uuid=%s)",
                telegram_id,
                position,
                candidate.gender,
                candidate.age,
                candidate.language,
                user_uuid_str[:8],
            )
            return True, "Enqueued successfully.", position

    async def _sync_blocklist_to_redis(self, telegram_id: int) -> None:
        """
        Mirror the user's PostgreSQL blocklist into a Redis SET for sub-ms lookups.
        Key: user:blocks:{telegram_id}
        """
        try:
            from app.core.moderation.service import ModerationService
            mod_service = ModerationService()
            blocked_ids = await mod_service.get_blocked_telegram_ids(telegram_id)

            client = self._get_client()
            redis_key = f"user:blocks:{telegram_id}"
            # Always rebuild the set (delete + sadd) to keep it fresh
            await client.delete(redis_key)
            if blocked_ids:
                await client.sadd(redis_key, *[str(bid) for bid in blocked_ids])
                await client.expire(redis_key, CANDIDATE_TTL)
        except Exception:
            logger.debug("Could not sync blocklist for user %s (non-critical)", telegram_id)

    async def dequeue(self, telegram_id: int) -> bool:
        """
        Remove a user from the matchmaking queue and return them to IDLE.

        Safe to call if the user is already not in the queue.
        """
        async with redis_lock(f"queue:{telegram_id}", timeout=5.0):
            client = self._get_client()
            await client.zrem(QUEUE_KEY, str(telegram_id))
            await client.delete(self._candidate_key(telegram_id))

            # Return user to IDLE if currently SEARCHING
            current = await self.state_manager.get_state(telegram_id)
            if current.is_searching:
                await self.state_manager.transition_to(
                    telegram_id=telegram_id,
                    target_state=UserMatchState.IDLE,
                    use_lock=False,
                )

            logger.info("User %s removed from matchmaking queue", telegram_id)
            return True

    async def get_queue_length(self) -> int:
        """Return the total number of users currently in the waiting queue."""
        client = self._get_client()
        return await client.zcard(QUEUE_KEY)

    async def get_queue_position(self, telegram_id: int) -> Optional[int]:
        """Return the 1-based rank of a user in the FIFO queue (None if not in queue)."""
        client = self._get_client()
        rank = await client.zrank(QUEUE_KEY, str(telegram_id))
        return (rank + 1) if rank is not None else None

    async def is_in_queue(self, telegram_id: int) -> bool:
        """Check if user currently has a score in the waiting ZSET."""
        client = self._get_client()
        score = await client.zscore(QUEUE_KEY, str(telegram_id))
        return score is not None

    async def get_candidate(self, telegram_id: int) -> Optional[QueueCandidate]:
        """Fetch cached candidate metadata for a user."""
        client = self._get_client()
        raw = await client.get(self._candidate_key(telegram_id))
        if not raw:
            return None
        try:
            return QueueCandidate.model_validate_json(raw)
        except Exception as e:
            logger.warning("Failed to parse candidate JSON for %s: %s", telegram_id, e)
            return None

    async def get_waiting_candidates_batched(self, limit: int = BATCH_SIZE) -> List[QueueCandidate]:
        """
        Retrieve up to `limit` waiting candidates in FIFO order using pipelined MGET.

        Optimizations over get_all_waiting_candidates():
          - Bounded retrieval: only fetches the `limit` oldest candidates
          - Single MGET call: collapses N Redis round-trips into 1 (~1ms)
          - Automatic stale pruning
        """
        client = self._get_client()

        # 1. Fetch bounded window of oldest queue members (FIFO order)
        members: list[str] = await client.zrange(QUEUE_KEY, 0, limit - 1)
        if not members:
            return []

        # 2. Build keys and fetch all candidate JSONs in a single MGET (1 round-trip)
        keys = [self._candidate_key(int(m)) for m in members]
        raw_values = await client.mget(*keys)

        # 3. Parse candidates and identify stale entries
        candidates: list[QueueCandidate] = []
        stale_members: list[str] = []

        for member, raw in zip(members, raw_values):
            if raw is not None:
                try:
                    candidates.append(QueueCandidate.model_validate_json(raw))
                except Exception as e:
                    logger.warning("Failed to parse candidate JSON for %s: %s", member, e)
                    stale_members.append(member)
            else:
                stale_members.append(member)

        # 4. Prune stale members whose candidate key expired
        if stale_members:
            await client.zrem(QUEUE_KEY, *stale_members)
            logger.info("Pruned %s stale members from matchmaking queue", len(stale_members))

        return candidates

    async def get_all_waiting_candidates(self) -> List[QueueCandidate]:
        """
        Retrieve all waiting candidates in FIFO order.

        Used by the matchmaking engine (Phase 9) to evaluate potential matches.
        Stale entries without metadata are automatically pruned.

        NOTE: Prefer get_waiting_candidates_batched() for production use.
        This method is kept for backwards compatibility with tests.
        """
        client = self._get_client()
        members: list[str] = await client.zrange(QUEUE_KEY, 0, -1)

        if not members:
            return []

        # Use MGET for efficient batch retrieval
        keys = [self._candidate_key(int(m)) for m in members]
        raw_values = await client.mget(*keys)

        candidates: list[QueueCandidate] = []
        stale_members: list[str] = []

        for member, raw in zip(members, raw_values):
            if raw is not None:
                try:
                    candidates.append(QueueCandidate.model_validate_json(raw))
                except Exception as e:
                    logger.warning("Failed to parse candidate JSON for %s: %s", member, e)
                    stale_members.append(member)
            else:
                stale_members.append(member)

        # Prune any stale members whose candidate key expired
        if stale_members:
            await client.zrem(QUEUE_KEY, *stale_members)
            logger.info("Pruned %s stale members from matchmaking queue", len(stale_members))

        return candidates
