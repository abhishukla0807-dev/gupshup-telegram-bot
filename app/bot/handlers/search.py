"""
/search and /stop command handlers — Matchmaking Queue operations.

Commands:
  /search — Adds user to the Redis waiting queue with score = now()
  /stop   — Cancels search and removes user from the waiting queue
  /cancel — Alias for /stop
"""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import (
    chat_controls_keyboard,
    idle_search_keyboard,
    search_waiting_keyboard,
)
from app.core.matching.queue_service import MatchmakingQueueService
from app.core.sessions.state_machine import UserStateData, UserStateManager
from app.infrastructure.rate_limiter import rate_limiter

router = Router(name="search")
queue_service = MatchmakingQueueService()
state_manager = UserStateManager()



# ──────────────────────────────────────────────
# /search command & button — join matchmaking queue
# ──────────────────────────────────────────────

@router.message(Command("search"))
@router.callback_query(F.data.in_(["start_search", "search_again"]))
async def cmd_search(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    user_match_state: UserStateData | None = None,
) -> None:
    """Enter the matchmaking queue."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    # Clear any active FSM dialog
    await state.clear()

    # Atomic token-bucket rate limit check (rl:search:{user_id})
    rl = await rate_limiter.check_search_rate_limit(telegram_user.id)
    if not rl.allowed:
        if isinstance(event, types.CallbackQuery):
            await event.answer(f"⏳ Please wait {rl.retry_after:.1f}s before searching again.", show_alert=True)
            return
        else:
            card = (
                "⏳ <b>SEARCH RATE LIMIT</b>\n"
                "───────────────────────────────\n"
                "You are initiating searches too frequently!\n\n"
                f"Please wait <b>{rl.retry_after:.1f}s</b> before searching again.\n"
                "───────────────────────────────"
            )
            await event.answer(card, parse_mode="HTML")
            return

    # Enqueue user
    success, reason, position = await queue_service.enqueue(telegram_user.id)


    if isinstance(event, types.CallbackQuery):
        await event.answer()
        respond = event.message.answer if event.message else None
    else:
        respond = event.answer

    if not respond:
        return

    if not success:
        if "active chat" in reason:
            card = (
                "💬 <b>ACTIVE CONVERSATION IN PROGRESS</b>\n"
                "───────────────────────────────\n"
                "You are already connected to a partner!\n\n"
                "To start a new search, switch or end this chat first.\n\n"
                "⚡️ <b>Quick Controls:</b>\n"
                "• <code>/next</code> — Skip to next match\n"
                "• <code>/end</code> — Leave conversation cleanly\n"
                "• <code>/block</code> — Block partner & leave\n"
                "───────────────────────────────"
            )
            await respond(card, reply_markup=chat_controls_keyboard(), parse_mode="HTML")
        elif "already in the matchmaking queue" in reason:
            queue_len = await queue_service.get_queue_length()
            card = (
                "⏳ <b>SEARCH ALREADY IN PROGRESS</b>\n"
                "───────────────────────────────\n"
                "You are actively in the matchmaking pool!\n\n"
                f"📍 <b>Queue Position:</b> <code>#{position}</code>\n"
                f"👥 <b>Waiting in Queue:</b> <code>{queue_len}</code>\n\n"
                "Please hold on, you'll be connected the moment a compatible match is found.\n"
                "───────────────────────────────\n"
                "<i>Tap below or use /stop to leave queue.</i>"
            )
            await respond(card, reply_markup=search_waiting_keyboard(), parse_mode="HTML")
        else:
            await respond(f"⚠️ {reason}")
        return

    queue_len = await queue_service.get_queue_length()
    card = (
        "🔎 <b>SEARCHING FOR A PARTNER...</b>\n"
        "───────────────────────────────\n"
        "Scanning candidates based on your profile & preferences:\n\n"
        f"📍 <b>Queue Position:</b> <code>#{position}</code>\n"
        f"👥 <b>Candidates Waiting:</b> <code>{queue_len}</code>\n"
        "⏱ <b>Waiting Priority:</b> <code>ACTIVE</code>\n\n"
        "💡 <i>Tip: The engine filters mutual preferences (gender, age bracket, language, blocks) before scoring compatibility.</i>\n"
        "───────────────────────────────\n"
        "⏳ <i>Hold tight, connecting you automatically...</i>"
    )

    await respond(card, reply_markup=search_waiting_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# /stop and /cancel commands — leave queue
# ──────────────────────────────────────────────

@router.message(Command(commands=["stop", "cancel"]))
async def cmd_stop_search(
    message: types.Message,
    state: FSMContext,
    user_match_state: UserStateData | None = None,
) -> None:
    """Leave the matchmaking queue or handle stop request."""
    telegram_user = message.from_user
    if telegram_user is None:
        return

    await state.clear()
    current_state = user_match_state or await state_manager.get_state(telegram_user.id)

    if current_state.is_searching:
        await queue_service.dequeue(telegram_user.id)
        card = (
            "🛑 <b>SEARCH CANCELLED</b>\n"
            "───────────────────────────────\n"
            "You have left the matchmaking queue.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "• You will not be paired until you search again.\n"
            "───────────────────────────────\n"
            "👇 <i>Tap below whenever you're ready to chat:</i>"
        )
        await message.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
    elif current_state.is_chatting:
        card = (
            "💬 <b>YOU ARE IN A LIVE CHAT</b>\n"
            "───────────────────────────────\n"
            "You cannot cancel search because you are currently chatting!\n\n"
            "• Use <code>/end</code> to leave this conversation.\n"
            "• Use <code>/next</code> to skip to another partner.\n"
            "───────────────────────────────"
        )
        await message.answer(card, reply_markup=chat_controls_keyboard(), parse_mode="HTML")
    else:
        card = (
            "ℹ️ <b>NOT IN QUEUE</b>\n"
            "───────────────────────────────\n"
            "You are not currently searching for a partner.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "───────────────────────────────\n"
            "👇 <i>Ready to meet someone new?</i>"
        )
        await message.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# Cancel search via inline button
# ──────────────────────────────────────────────

@router.callback_query(F.data == "search_cancel")
async def on_search_cancel_tapped(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    """User tapped 'Cancel Search' button."""
    telegram_user = callback.from_user
    await state.clear()

    await queue_service.dequeue(telegram_user.id)
    await callback.answer("Search cancelled.")

    if callback.message:
        card = (
            "🛑 <b>SEARCH CANCELLED</b>\n"
            "───────────────────────────────\n"
            "You have left the matchmaking queue.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "• You will not be paired until you search again.\n"
            "───────────────────────────────\n"
            "👇 <i>Tap below whenever you're ready to chat:</i>"
        )
        await callback.message.edit_text(
            card,
            reply_markup=idle_search_keyboard(),
            parse_mode="HTML",
        )
