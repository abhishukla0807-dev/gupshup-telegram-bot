"""
Unit and integration tests for matchmaking graceful degradation and strict queue timeout.

Verifies:
1. Strict hard constraints are enforced during the first 10 seconds of searching.
2. If hard constraints cannot be satisfied after 10 seconds, the engine dynamically
   relaxes demographic constraints and falls back to weighted scoring (best candidate).
3. If both strict and non-strict candidates exist, strict hard constraint match takes priority.
4. Blocked users and self-matches are NEVER relaxed under any circumstance.
5. Users waiting longer than 1 minute (60s) in the queue are dequeued and notified with Search Again options.
"""
import asyncio
import sys
import time
from unittest.mock import AsyncMock

sys.path.insert(0, "a:/telegram-matchmaker")

import pytest
from sqlalchemy import text

from app.bot.keyboards import search_timeout_keyboard
from app.core.matching.engine import MatchmakingConfig, MatchmakingEngine
from app.core.matching.queue_service import CANDIDATE_PREFIX, QUEUE_KEY, MatchmakingQueueService
from app.core.matching.schemas import QueueCandidate
from app.core.matching.worker import MatchmakingWorker
from app.core.moderation.service import ModerationService
from app.core.sessions.service import SessionService
from app.core.sessions.state_machine import UserMatchState, UserStateManager
from app.core.users.service import UserService
from app.infrastructure.database import AsyncSessionLocal
from app.infrastructure.redis import get_redis_client, init_redis


async def _clean_state():
    """Ensure Redis queue and test user states are clean."""
    await init_redis()
    client = get_redis_client()
    await client.delete(QUEUE_KEY)

    test_ids = [800001, 800002, 800003, 800004]
    for uid in test_ids:
        await client.delete(
            f"user:state:{uid}",
            f"{CANDIDATE_PREFIX}{uid}",
            f"matchmaking:claimed:{uid}",
            f"user:blocks:{uid}",
        )

    # Clean DB test sessions
    async with AsyncSessionLocal() as db_session:
        await db_session.execute(
            text(
                f"DELETE FROM user_active_sessions WHERE user_id IN "
                f"(SELECT id FROM users WHERE telegram_id IN ({','.join(map(str, test_ids))}))"
            )
        )
        await db_session.execute(
            text(
                f"DELETE FROM chat_sessions WHERE user1_id IN "
                f"(SELECT id FROM users WHERE telegram_id IN ({','.join(map(str, test_ids))})) "
                f"OR user2_id IN (SELECT id FROM users WHERE telegram_id IN ({','.join(map(str, test_ids))}))"
            )
        )
        await db_session.commit()


async def _enqueue_mock_candidate(
    tg_id: int,
    gender: str = "male",
    preferred_gender: str = "female",
    age: int = 25,
    preferred_age_min: int = 20,
    preferred_age_max: int = 30,
    language: str = "en",
    preferred_language: str = "en",
    interests: list = None,
    location: str = "New York",
    wait_time_seconds: float = 0.0,
) -> QueueCandidate:
    """Helper to inject a candidate into Redis and DB with a custom joined_at timestamp."""
    user_service = UserService()
    user, _ = await user_service.register_or_fetch(tg_id, f"User_{tg_id}")
    user_uuid_str = str(user.id) if user else ""

    client = get_redis_client()
    state_manager = UserStateManager()

    now = time.time()
    joined_at = now - wait_time_seconds

    candidate = QueueCandidate(
        telegram_id=tg_id,
        user_uuid=user_uuid_str,
        gender=gender,
        age=age,
        language=language,
        interests=interests or ["coding"],
        location=location,
        media_enabled=True,
        preferred_gender=preferred_gender,
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
        preferred_language=preferred_language,
        joined_at=joined_at,
    )

    await client.set(f"{CANDIDATE_PREFIX}{tg_id}", candidate.model_dump_json(), ex=3600)
    await client.zadd(QUEUE_KEY, {str(tg_id): joined_at})
    await state_manager.transition_to(
        telegram_id=tg_id,
        target_state=UserMatchState.SEARCHING,
        use_lock=False,
    )
    return candidate


@pytest.mark.asyncio
async def test_strict_hard_constraints_within_10_seconds():
    """Candidates waiting < 10s must strictly satisfy hard constraints. Incompatible candidates do NOT match."""
    await _clean_state()

    # Two male candidates seeking female, waited only 3 seconds
    await _enqueue_mock_candidate(
        800001, gender="male", preferred_gender="female", wait_time_seconds=3.0
    )
    await _enqueue_mock_candidate(
        800002, gender="male", preferred_gender="female", wait_time_seconds=3.0
    )

    engine = MatchmakingEngine()
    matches = await engine.match_candidates()

    assert len(matches) == 0, "Candidates waiting < 10s must not match if hard constraints fail."


@pytest.mark.asyncio
async def test_dynamic_relaxation_to_weighted_scoring_after_10_seconds():
    """Candidates waiting >= 10s should relax hard constraints and match via weighted scoring."""
    await _clean_state()

    # Two male candidates seeking female, waited 15 seconds (> 10s)
    await _enqueue_mock_candidate(
        800001,
        gender="male",
        preferred_gender="female",
        interests=["technology", "movies"],
        wait_time_seconds=15.0,
    )
    await _enqueue_mock_candidate(
        800002,
        gender="male",
        preferred_gender="female",
        interests=["technology", "gaming"],
        wait_time_seconds=15.0,
    )

    engine = MatchmakingEngine()
    matches = await engine.match_candidates()

    assert len(matches) == 1, "Candidates waiting >= 10s should match via weighted scoring."
    cand_a, cand_b, sess_id = matches[0]
    matched_ids = {cand_a.telegram_id, cand_b.telegram_id}
    assert matched_ids == {800001, 800002}
    assert sess_id is not None


@pytest.mark.asyncio
async def test_hard_constraint_priority_over_relaxed_match():
    """
    If candidate A waited >= 10s, but there is both a strict match and a relaxed match,
    strict hard constraint match MUST take priority!
    """
    await _clean_state()

    # Candidate 1: Male, 25, seeking Female (waited 15s)
    await _enqueue_mock_candidate(
        800001,
        gender="male",
        preferred_gender="female",
        interests=["python"],
        wait_time_seconds=15.0,
    )
    # Candidate 2: Male, 25, seeking Female (incompatible gender, waited 2s, high interest overlap)
    await _enqueue_mock_candidate(
        800002,
        gender="male",
        preferred_gender="female",
        interests=["python", "ai", "music"],
        wait_time_seconds=2.0,
    )
    # Candidate 3: Female, 25, seeking Male (compatible gender, waited 2s)
    await _enqueue_mock_candidate(
        800003,
        gender="female",
        preferred_gender="male",
        interests=["reading"],
        wait_time_seconds=2.0,
    )

    engine = MatchmakingEngine()
    matches = await engine.match_candidates()

    assert len(matches) == 1
    cand_a, cand_b, sess_id = matches[0]
    matched_ids = {cand_a.telegram_id, cand_b.telegram_id}
    assert matched_ids == {800001, 800003}, "Strict hard-constraint match (800003) must take priority!"


@pytest.mark.asyncio
async def test_blocks_never_relaxed_even_after_timeout():
    """Mutual blocks must NEVER be relaxed, even if candidates waited > 10s."""
    await _clean_state()
    client = get_redis_client()
    await _enqueue_mock_candidate(800001, wait_time_seconds=25.0)
    await _enqueue_mock_candidate(800002, wait_time_seconds=25.0)

    # Set mutual block in Redis
    await client.sadd("user:blocks:800001", "800002")

    engine = MatchmakingEngine()
    matches = await engine.match_candidates()

    assert len(matches) == 0, "Blocked users must NEVER be matched, regardless of wait time."


@pytest.mark.asyncio
async def test_queue_timeout_after_60_seconds():
    """A user waiting > 60 seconds is automatically dequeued and notified."""
    await _clean_state()
    client = get_redis_client()
    state_manager = UserStateManager()

    # User waited 65 seconds (> 60s)
    await _enqueue_mock_candidate(800001, wait_time_seconds=65.0)
    # User waited 20 seconds (< 60s)
    await _enqueue_mock_candidate(800002, wait_time_seconds=20.0)

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    worker = MatchmakingWorker(bot=mock_bot)
    timed_out_count = await worker.handle_queue_timeouts()

    assert timed_out_count == 1, "Only candidate waiting > 60s should time out."

    # Verify 800001 is removed from queue and transitioned to IDLE
    score_800001 = await client.zscore(QUEUE_KEY, "800001")
    assert score_800001 is None, "Timed out user must be removed from waiting ZSET."

    state_800001 = await state_manager.get_state(800001)
    assert state_800001.state == UserMatchState.IDLE, "Timed out user state must be IDLE."

    # Verify 800002 remains in queue and SEARCHING
    score_800002 = await client.zscore(QUEUE_KEY, "800002")
    assert score_800002 is not None, "Candidate waiting < 60s must remain in queue."

    state_800002 = await state_manager.get_state(800002)
    assert state_800002.state == UserMatchState.SEARCHING

    # Verify bot sent the polite notification to 800001
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 800001
    assert "MATCHMAKING TIMEOUT" in call_kwargs["text"]
    assert "1 minute" in call_kwargs["text"]
