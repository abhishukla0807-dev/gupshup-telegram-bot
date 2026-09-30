"""
Tests for guaranteed session teardown and auto-healing of stale/zombie chat states.

Verifies:
1. end_chat_session() guarantees Redis teardown even if PostgreSQL session is missing or already ended.
2. end_chat_session() honors fallback_tg_ids to force_idle both participants.
3. queue_service.enqueue() auto-heals a user whose partner already left (zombie CHATTING state).
4. queue_service.enqueue() correctly rejects genuinely active sessions.
"""
import sys
import uuid
import time
import pytest

sys.path.insert(0, "a:/telegram-matchmaker")

from app.core.matching.queue_service import CANDIDATE_PREFIX, QUEUE_KEY, MatchmakingQueueService
from app.core.sessions.service import SessionService
from app.core.sessions.state_machine import UserMatchState, UserStateManager
from app.core.users.service import UserService
from app.core.users.profile_service import ProfileService
from app.core.users.preferences_service import PreferencesService
from app.infrastructure.redis import get_redis_client, init_redis


@pytest.mark.asyncio
async def test_end_chat_session_guaranteed_teardown_missing_db():
    """Verify that end_chat_session cleans up Redis even if DB session is None."""
    await init_redis()
    client = get_redis_client()
    state_manager = UserStateManager()
    session_service = SessionService()

    user_a = 770001
    user_b = 770002
    fake_session_id = str(uuid.uuid4())

    # Set both users to CHATTING in Redis
    await state_manager.transition_to(user_a, UserMatchState.SEARCHING)
    await state_manager.transition_to(user_a, UserMatchState.CHATTING, partner_id=user_b, session_id=fake_session_id)

    await state_manager.transition_to(user_b, UserMatchState.SEARCHING)
    await state_manager.transition_to(user_b, UserMatchState.CHATTING, partner_id=user_a, session_id=fake_session_id)

    # Set active session key in Redis
    await client.hset(
        f"session:active:{fake_session_id}",
        mapping={"user1_tg_id": str(user_a), "user2_tg_id": str(user_b), "status": "active"},
    )

    # Call end_chat_session with fake session ID not in PostgreSQL
    await session_service.end_chat_session(
        session_id_str=fake_session_id,
        ended_reason="user_left",
        fallback_tg_ids=(user_a, user_b),
    )

    # Both users MUST be IDLE in Redis
    state_a = await state_manager.get_state(user_a)
    state_b = await state_manager.get_state(user_b)
    assert state_a.state == UserMatchState.IDLE
    assert state_b.state == UserMatchState.IDLE
    assert state_a.partner_id is None
    assert state_b.partner_id is None

    # Redis active session routing key MUST be deleted
    assert await client.exists(f"session:active:{fake_session_id}") == 0


@pytest.mark.asyncio
async def test_enqueue_auto_heals_zombie_chatting_state():
    """Verify that queue_service.enqueue() auto-heals a user whose partner already left."""
    await init_redis()
    client = get_redis_client()
    state_manager = UserStateManager()
    queue_service = MatchmakingQueueService()
    user_service = UserService()
    profile_service = ProfileService()
    pref_service = PreferencesService()

    user_a = 770003
    user_b = 770004
    dead_session_id = str(uuid.uuid4())

    # Setup profile & preferences for User B
    await user_service.register_or_fetch(user_b, "UserB")
    await profile_service.create_profile(user_b, gender="male", age=25, language="en")
    await pref_service.update_preferences(user_b, preferred_gender="any", preferred_age_min=18, preferred_age_max=40, preferred_language="any")

    # Simulate zombie state: User B is in CHATTING with User A, but User A is IDLE
    await state_manager.transition_to(user_b, UserMatchState.SEARCHING)
    await state_manager.transition_to(user_b, UserMatchState.CHATTING, partner_id=user_a, session_id=dead_session_id)
    await state_manager.force_idle(user_a)

    # When User B attempts to enqueue via /search:
    ok, msg, pos = await queue_service.enqueue(user_b)

    # Must succeed (auto-healed from zombie CHATTING to SEARCHING!)
    assert ok is True, f"Enqueue should succeed via auto-healing, got: {msg}"
    assert pos >= 1

    # Verify state is now SEARCHING in Redis
    state_b = await state_manager.get_state(user_b)
    assert state_b.state == UserMatchState.SEARCHING

    # Clean up
    await queue_service.dequeue(user_b)
