"""
Matchmaking Worker & Self-Healing Reconciliation Janitor.

Runs background loops for continuous matching and automated consistency reconciliation.
"""
import asyncio
import logging
import time
from typing import Optional

from aiogram import Bot
from sqlalchemy import select

from app.bot.keyboards import search_timeout_keyboard
from app.core.matching.engine import MatchmakingEngine
from app.core.matching.queue_service import QUEUE_KEY, MatchmakingQueueService
from app.core.sessions.models import UserActiveSession
from app.core.sessions.state_machine import UserMatchState, UserStateManager
from app.core.users.repository import UserRepository
from app.infrastructure.database import AsyncSessionLocal
from app.infrastructure.redis import get_redis_client

logger = logging.getLogger(__name__)


def match_notification_keyboard():
    """Inline keyboard for an active chat session."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭️ Next Partner", callback_data="chat_next"),
                InlineKeyboardButton(text="🛑 End Chat", callback_data="chat_end"),
            ],
            [
                InlineKeyboardButton(text="🚫 Block Partner", callback_data="chat_block"),
            ],
        ]
    )


class MatchmakingWorker:
    """Continuous background worker for matching waiting candidates."""

    def __init__(
        self,
        engine: MatchmakingEngine | None = None,
        bot: Bot | None = None,
        tick_interval_seconds: float = 0.5,
        queue_service: MatchmakingQueueService | None = None,
    ) -> None:
        self.engine = engine or MatchmakingEngine()
        self.queue_service = queue_service or self.engine.queue_service
        self.bot = bot
        self.tick_interval = tick_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background matching loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="matchmaking_worker")
        logger.info("MatchmakingWorker started with tick interval %.2fs", self.tick_interval)

    async def stop(self) -> None:
        """Stop the background matching loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MatchmakingWorker stopped.")

    async def _run_loop(self) -> None:
        """Main evaluation loop."""
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in matchmaking worker loop")

            await asyncio.sleep(self.tick_interval)

    async def tick(self) -> int:
        """Execute one matching pass, notify matched candidates, and handle queue timeouts."""
        matched_pairs = await self.engine.match_candidates()
        if matched_pairs and self.bot is not None:
            for cand_a, cand_b, session_id in matched_pairs:
                await self._notify_user(cand_a.telegram_id, cand_b)
                await self._notify_user(cand_b.telegram_id, cand_a)

        # Handle queue timeouts for candidates waiting longer than MAX_QUEUE_WAIT_SECONDS (1 minute)
        await self.handle_queue_timeouts()

        return len(matched_pairs) if matched_pairs else 0

    async def handle_queue_timeouts(self) -> int:
        """
        Scan waiting queue for candidates who have exceeded MAX_QUEUE_WAIT_SECONDS (1 minute).
        Atomically dequeues each timed-out user, transitions state to IDLE, and sends
        a timeout notification with options to search again or tweak preferences.
        """
        max_wait = self.engine.config.MAX_QUEUE_WAIT_SECONDS
        client = self.engine._get_client()
        now = time.time()
        cutoff = now - max_wait

        # Fetch all user IDs in the queue with score <= cutoff (joined_at <= now - 60s)
        timed_out_ids_raw = await client.zrange(QUEUE_KEY, "-inf", str(cutoff), byscore=True)
        if not timed_out_ids_raw:
            return 0

        timeout_count = 0
        for raw_id in timed_out_ids_raw:
            try:
                tg_id = int(raw_id)
            except (ValueError, TypeError):
                continue

            # Skip candidate if currently claimed by a worker for an active handshake
            if await client.exists(f"matchmaking:claimed:{tg_id}"):
                continue

            # Dequeue candidate: removes from ZSET, deletes candidate cache, sets state to IDLE
            await self.queue_service.dequeue(tg_id)
            timeout_count += 1
            logger.info("User %s timed out after waiting > %.1fs in matchmaking queue", tg_id, max_wait)

            # Send timeout notification via Telegram
            if self.bot is not None:
                await self._notify_timeout(tg_id)

        return timeout_count

    async def _notify_timeout(self, recipient_id: int) -> None:
        """Send timeout alert to a user who waited longer than max wait time."""
        if self.bot is None:
            return
        text = (
            "⏳ <b>MATCHMAKING TIMEOUT</b>\n"
            "───────────────────────────────\n"
            "You've been waiting for over 1 minute, but no matching partners are currently available.\n\n"
            "💡 <b>Tips:</b>\n"
            "• Tap <b>🔄 Search Again</b> to re-enter the queue.\n"
            "• Or broaden your search criteria in <b>⚙️ Preferences</b> (e.g., select 'Any Gender' or wider age brackets).\n"
            "───────────────────────────────"
        )
        try:
            await self.bot.send_message(
                chat_id=recipient_id,
                text=text,
                reply_markup=search_timeout_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Could not send timeout notification to user %s: %s", recipient_id, e)


    async def _notify_user(self, recipient_id: int, partner_cand) -> None:
        """Send connection notification to a matched user."""
        if self.bot is None:
            return
        g_icons = {"male": "👨", "female": "👩", "other": "🧑"}
        g_icon = g_icons.get(partner_cand.gender, "👤")
        lang_map = {
            "en": "🇬🇧 English",
            "hi": "🇮🇳 Hindi",
            "es": "🇪🇸 Spanish",
            "fr": "🇫🇷 French",
            "de": "🇩🇪 German",
            "ru": "🇷🇺 Russian",
        }
        lang_label = lang_map.get(partner_cand.language, partner_cand.language.upper())

        text = (
            "🎉 <b>MATCH FOUND! YOU ARE CONNECTED</b>\n"
            "───────────────────────────────\n"
            "Say hello to your new anonymous conversation partner!\n\n"
            "👤 <b>PARTNER PROFILE:</b>\n"
            f"• <b>Gender:</b> {g_icon} <code>{partner_cand.gender.capitalize()}</code>\n"
            f"• <b>Age Bracket:</b> 📅 <code>~{partner_cand.age} years</code>\n"
            f"• <b>Language:</b> 🌐 <code>{lang_label}</code>\n\n"
            "🔒 <b>PRIVACY & INVARIANTS:</b>\n"
            "• Both Telegram accounts remain completely private.\n"
            "• Messages, voice notes, photos & media are relayed securely.\n"
            "───────────────────────────────\n"
            "👇 <i>Use the controls below or commands (/next, /end, /block):</i>"
        )
        try:
            await self.bot.send_message(
                chat_id=recipient_id,
                text=text,
                reply_markup=match_notification_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Could not send match notification to user %s: %s", recipient_id, e)


class ReconciliationJanitor:
    """
    Self-healing background daemon for resolving state inconsistencies
    between Redis and PostgreSQL, cleaning stale claims, and maintaining queue health.
    """

    def __init__(
        self,
        interval_seconds: float = 60.0,
        state_manager: UserStateManager | None = None,
        queue_service: MatchmakingQueueService | None = None,
    ) -> None:
        self.interval = interval_seconds
        self.state_manager = state_manager or UserStateManager()
        self.queue_service = queue_service or MatchmakingQueueService()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the reconciliation janitor loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="reconciliation_janitor")
        logger.info("ReconciliationJanitor started with interval %.1fs", self.interval)

    async def stop(self) -> None:
        """Stop the reconciliation janitor loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ReconciliationJanitor stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in reconciliation janitor pass")

            await asyncio.sleep(self.interval)

    async def reconcile(self) -> None:
        """Execute one complete consistency audit pass."""
        client = get_redis_client()

        # 1. Clean up stale / abandoned queue members
        queue_members: list[str] = await client.zrange(QUEUE_KEY, 0, -1)
        for member in queue_members:
            tg_id = int(member)
            has_cand = await client.exists(f"matchmaking:candidate:{tg_id}")
            state = await self.state_manager.get_state(tg_id)

            # If metadata expired or user is no longer searching, remove from queue
            if not has_cand or state.state != UserMatchState.SEARCHING:
                await client.zrem(QUEUE_KEY, member)
                logger.info("Janitor removed invalid queue member: %s (state=%s)", tg_id, state.state.value)

        # 2. Reconcile Redis vs PostgreSQL active sessions
        async with AsyncSessionLocal() as session:
            # Query all active sessions from PostgreSQL
            stmt = select(UserActiveSession)
            result = await session.execute(stmt)
            active_rows = result.scalars().all()

            from app.core.sessions.repository import SessionRepository
            user_repo = UserRepository(session)
            session_repo = SessionRepository(session)
            active_tg_ids = set()

            for row in active_rows:
                u = await user_repo.get_by_id(row.user_id)
                if u:
                    active_tg_ids.add(u.telegram_id)
                    # Ensure Redis state reflects CHATTING
                    redis_state = await self.state_manager.get_state(u.telegram_id)
                    if redis_state.state != UserMatchState.CHATTING or not redis_state.partner_id:
                        chat_sess = await session_repo.get_by_id(row.session_id)
                        partner_tg_id = None
                        if chat_sess:
                            p_uuid = chat_sess.user2_id if chat_sess.user1_id == u.id else chat_sess.user1_id
                            p_user = await user_repo.get_by_id(p_uuid)
                            if p_user:
                                partner_tg_id = p_user.telegram_id

                        logger.warning(
                            "Janitor rehydrating lost Redis state to CHATTING for active user: %s (partner=%s)",
                            u.telegram_id,
                            partner_tg_id,
                        )
                        await self.state_manager.rehydrate_chatting(
                            telegram_id=u.telegram_id,
                            session_id=str(row.session_id),
                            partner_id=partner_tg_id,
                        )



        logger.debug("Reconciliation pass completed successfully.")
