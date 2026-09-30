"""
User Match State Machine — tracks runtime presence and matchmaking states in Redis.

States:
  - IDLE: User is not searching, not in queue, not in chat.
  - SEARCHING: User is waiting in the matchmaking queue for a partner.
  - CHATTING: User is connected to an anonymous partner in an active chat session.

State Transitions:
  - IDLE -> SEARCHING           (User initiates /search)
  - SEARCHING -> IDLE           (User cancels /stop or queue timeout)
  - SEARCHING -> CHATTING       (Matchmaker pairs user with partner)
  - CHATTING -> IDLE            (User runs /end or /stop, or partner leaves)
  - CHATTING -> SEARCHING       (User runs /next to skip to next partner)
  - ANY -> IDLE                 (force_idle for cleanup or error recovery)
"""
import logging
import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.infrastructure.redis import get_redis_client, redis_lock

logger = logging.getLogger(__name__)


class UserMatchState(str, Enum):
    """Runtime matchmaker state of a user."""
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    CHATTING = "CHATTING"


# Strict map of permissible transitions
ALLOWED_TRANSITIONS: dict[UserMatchState, set[UserMatchState]] = {
    UserMatchState.IDLE: {UserMatchState.SEARCHING},
    UserMatchState.SEARCHING: {UserMatchState.IDLE, UserMatchState.CHATTING},
    UserMatchState.CHATTING: {UserMatchState.IDLE, UserMatchState.SEARCHING},
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        current_state: UserMatchState,
        target_state: UserMatchState,
        telegram_id: int,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        self.telegram_id = telegram_id
        super().__init__(
            f"User {telegram_id}: illegal state transition from "
            f"'{current_state.value}' to '{target_state.value}'."
        )


class UserStateData(BaseModel):
    """Structured representation of a user's runtime state."""
    telegram_id: int
    state: UserMatchState = UserMatchState.IDLE
    partner_id: Optional[int] = None
    session_id: Optional[str] = None
    entered_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @property
    def is_idle(self) -> bool:
        return self.state == UserMatchState.IDLE

    @property
    def is_searching(self) -> bool:
        return self.state == UserMatchState.SEARCHING

    @property
    def is_chatting(self) -> bool:
        return self.state == UserMatchState.CHATTING


class UserStateManager:
    """
    Manages user state stored in Redis.

    Uses Redis Hashes for atomic, O(1) field lookups and updates.
    Key format: user:state:{telegram_id}
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _get_client(self) -> Redis:
        return self._redis or get_redis_client()

    @staticmethod
    def _state_key(telegram_id: int) -> str:
        return f"user:state:{telegram_id}"

    async def get_state(self, telegram_id: int) -> UserStateData:
        """
        Fetch the current state of a user from Redis.

        If no record exists, defaults to IDLE.
        """
        client = self._get_client()
        raw = await client.hgetall(self._state_key(telegram_id))

        if not raw:
            now = time.time()
            return UserStateData(
                telegram_id=telegram_id,
                state=UserMatchState.IDLE,
                partner_id=None,
                session_id=None,
                entered_at=now,
                updated_at=now,
            )

        state_str = raw.get("state", UserMatchState.IDLE.value)
        try:
            state_enum = UserMatchState(state_str)
        except ValueError:
            state_enum = UserMatchState.IDLE

        partner_id_str = raw.get("partner_id")
        partner_id = int(partner_id_str) if partner_id_str else None

        session_id = raw.get("session_id") or None
        entered_at = float(raw.get("entered_at", time.time()))
        updated_at = float(raw.get("updated_at", time.time()))

        return UserStateData(
            telegram_id=telegram_id,
            state=state_enum,
            partner_id=partner_id,
            session_id=session_id,
            entered_at=entered_at,
            updated_at=updated_at,
        )

    async def transition_to(
        self,
        telegram_id: int,
        target_state: UserMatchState,
        partner_id: int | None = None,
        session_id: str | None = None,
        use_lock: bool = True,
    ) -> UserStateData:
        """
        Safely transition a user to target_state with validation and distributed locking.

        Raises InvalidStateTransitionError if the transition is prohibited.
        """
        if use_lock:
            async with redis_lock(f"state:{telegram_id}") as acquired:
                if not acquired:
                    raise RuntimeError(
                        f"Could not acquire state lock for telegram_id={telegram_id}"
                    )
                return await self._execute_transition(
                    telegram_id, target_state, partner_id, session_id
                )
        else:
            return await self._execute_transition(
                telegram_id, target_state, partner_id, session_id
            )

    async def _execute_transition(
        self,
        telegram_id: int,
        target_state: UserMatchState,
        partner_id: int | None = None,
        session_id: str | None = None,
    ) -> UserStateData:
        current = await self.get_state(telegram_id)

        # Idempotent no-op if already in target state
        if current.state == target_state:
            return current

        # Validate transition
        allowed = ALLOWED_TRANSITIONS.get(current.state, set())

        if target_state not in allowed:
            logger.warning(
                "Rejected state transition for telegram_id=%s: %s -> %s",
                telegram_id,
                current.state.value,
                target_state.value,
            )
            raise InvalidStateTransitionError(current.state, target_state, telegram_id)

        now = time.time()
        client = self._get_client()
        key = self._state_key(telegram_id)

        mapping = {
            "state": target_state.value,
            "partner_id": str(partner_id) if partner_id is not None else "",
            "session_id": str(session_id) if session_id is not None else "",
            "entered_at": str(now),
            "updated_at": str(now),
        }

        await client.hset(key, mapping=mapping)
        logger.info(
            "State transition for telegram_id=%s: %s -> %s (partner=%s, session=%s)",
            telegram_id,
            current.state.value,
            target_state.value,
            partner_id,
            session_id,
        )

        return UserStateData(
            telegram_id=telegram_id,
            state=target_state,
            partner_id=partner_id,
            session_id=session_id,
            entered_at=now,
            updated_at=now,
        )

    async def force_idle(self, telegram_id: int) -> UserStateData:
        """
        Unconditionally reset a user to IDLE state.

        Used for error recovery, queue timeouts, or administrative resets.
        Bypasses normal state transition validation.
        """
        async with redis_lock(f"state:{telegram_id}"):
            now = time.time()
            client = self._get_client()
            key = self._state_key(telegram_id)

            mapping = {
                "state": UserMatchState.IDLE.value,
                "partner_id": "",
                "session_id": "",
                "entered_at": str(now),
                "updated_at": str(now),
            }
            await client.hset(key, mapping=mapping)
            logger.info("User %s force reset to IDLE", telegram_id)

            return UserStateData(
                telegram_id=telegram_id,
                state=UserMatchState.IDLE,
                partner_id=None,
                session_id=None,
                entered_at=now,
                updated_at=now,
            )

    async def rehydrate_chatting(
        self,
        telegram_id: int,
        session_id: str,
        partner_id: int | None = None,
    ) -> UserStateData:
        """
        Directly restore a user to CHATTING state during reconciliation or crash recovery.

        Bypasses normal state machine transition restrictions because PostgreSQL
        already holds the durable active session record.
        """
        async with redis_lock(f"state:{telegram_id}"):
            now = time.time()
            client = self._get_client()
            key = self._state_key(telegram_id)

            mapping = {
                "state": UserMatchState.CHATTING.value,
                "partner_id": str(partner_id) if partner_id is not None else "",
                "session_id": str(session_id),
                "entered_at": str(now),
                "updated_at": str(now),
            }
            await client.hset(key, mapping=mapping)
            logger.info("User %s rehydrated to CHATTING (session=%s, partner=%s)", telegram_id, session_id, partner_id)

            return UserStateData(
                telegram_id=telegram_id,
                state=UserMatchState.CHATTING,
                partner_id=partner_id,
                session_id=session_id,
                entered_at=now,
                updated_at=now,
            )


    async def get_partner_id(self, telegram_id: int) -> int | None:
        """Fast direct lookup of a user's active chat partner Telegram ID."""
        client = self._get_client()
        raw_val = await client.hget(self._state_key(telegram_id), "partner_id")
        return int(raw_val) if raw_val else None

    async def get_session_id(self, telegram_id: int) -> str | None:
        """Fast direct lookup of a user's active session ID."""
        client = self._get_client()
        raw_val = await client.hget(self._state_key(telegram_id), "session_id")
        return str(raw_val) if raw_val else None

    async def is_idle(self, telegram_id: int) -> bool:
        """Check if user is currently IDLE."""
        client = self._get_client()
        state = await client.hget(self._state_key(telegram_id), "state")
        return state is None or state == UserMatchState.IDLE.value

    async def is_searching(self, telegram_id: int) -> bool:
        """Check if user is currently SEARCHING in queue."""
        client = self._get_client()
        state = await client.hget(self._state_key(telegram_id), "state")
        return state == UserMatchState.SEARCHING.value

    async def is_chatting(self, telegram_id: int) -> bool:
        """Check if user is currently in an active CHATTING session."""
        client = self._get_client()
        state = await client.hget(self._state_key(telegram_id), "state")
        return state == UserMatchState.CHATTING.value
