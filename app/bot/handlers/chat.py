"""
Chat router & message relay — Anonymous communication and session controls.

Security Invariants:
  1. The client NEVER specifies the recipient. The recipient is derived
     strictly from the active ChatSession in Redis/PostgreSQL.
  2. All messages are relayed cleanly without forward headers, preserving full anonymity.
  3. Commands (/next, /end, /block) operate atomically on active sessions.
"""
import logging
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import idle_search_keyboard, search_waiting_keyboard
from app.core.matching.queue_service import MatchmakingQueueService
from app.core.moderation.service import ModerationService
from app.core.sessions.service import SessionService
from app.core.sessions.state_machine import UserStateData, UserStateManager

from app.core.moderation.content_moderation import ContentModerationService, content_moderator
from app.infrastructure.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


router = Router(name="chat")
session_service = SessionService()
state_manager = UserStateManager()
queue_service = MatchmakingQueueService()
moderation_service = ModerationService()



# ──────────────────────────────────────────────
# /next command & callback — skip to next partner
# ──────────────────────────────────────────────

@router.message(Command("next"))
@router.callback_query(F.data == "chat_next")
async def handle_next(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    user_match_state: UserStateData | None = None,
) -> None:
    """End the current chat session and immediately queue for a new partner."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    await state.clear()
    current_state = user_match_state or await state_manager.get_state(telegram_user.id)

    if not current_state.is_chatting or not current_state.session_id:
        card = (
            "ℹ️ <b>NO ACTIVE CONVERSATION</b>\n"
            "───────────────────────────────\n"
            "You are not in an active chat session.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "───────────────────────────────\n"
            "👇 <i>Find a partner using the button below:</i>"
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        else:
            await event.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        return

    session_id = current_state.session_id or ""
    partner_id = current_state.partner_id

    fallback_ids = (telegram_user.id, partner_id) if partner_id else (telegram_user.id,)

    # 1. Atomically terminate session with guaranteed Redis teardown
    await session_service.end_chat_session(
        session_id_str=session_id,
        ended_reason="next",
        requeue_tg_id=telegram_user.id,
        fallback_tg_ids=fallback_ids,
    )

    # 1b. Unconditionally guarantee partner is returned to IDLE
    if partner_id:
        await state_manager.force_idle(partner_id)

    # 2. Notify the other partner that the chat ended
    if partner_id and event.bot:
        try:
            partner_card = (
                "👋 <b>PARTNER DISCONNECTED</b>\n"
                "───────────────────────────────\n"
                "Your chat partner skipped to find another match.\n\n"
                "• Status: ⚪️ <code>IDLE</code>\n"
                "───────────────────────────────\n"
                "👇 <i>Find your next match below:</i>"
            )
            await event.bot.send_message(
                chat_id=partner_id,
                text=partner_card,
                reply_markup=idle_search_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug("Could not notify partner %s of /next: %s", partner_id, e)

    # 3. Automatically re-enqueue user into matchmaking queue
    success, reason, pos = await queue_service.enqueue(telegram_user.id)
    requeue_card = (
        "⏭ <b>SWITCHING PARTNERS</b>\n"
        "───────────────────────────────\n"
        "Previous chat closed. Searching for your next match!\n\n"
        f"📍 <b>Queue Position:</b> <code>#{pos}</code>\n"
        "⏳ <i>Hold tight, connecting you automatically...</i>\n"
        "───────────────────────────────\n"
        "<i>Use /stop or tap below to cancel search.</i>"
    )

    if isinstance(event, types.CallbackQuery):
        await event.answer("Finding next partner...")
        if event.message:
            await event.message.answer(requeue_card, reply_markup=search_waiting_keyboard(), parse_mode="HTML")
    else:
        await event.answer(requeue_card, reply_markup=search_waiting_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# /end command & callback — leave conversation
# ──────────────────────────────────────────────

@router.message(Command("end"))
@router.callback_query(F.data == "chat_end")
async def handle_end(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    user_match_state: UserStateData | None = None,
) -> None:
    """End the current chat session cleanly."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    await state.clear()
    current_state = user_match_state or await state_manager.get_state(telegram_user.id)

    if not current_state.is_chatting or not current_state.session_id:
        card = (
            "ℹ️ <b>NO ACTIVE CONVERSATION</b>\n"
            "───────────────────────────────\n"
            "You are not currently in an active chat.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "───────────────────────────────\n"
            "👇 <i>Find a partner using the button below:</i>"
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        else:
            await event.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        return

    session_id = current_state.session_id or ""
    partner_id = current_state.partner_id

    fallback_ids = (telegram_user.id, partner_id) if partner_id else (telegram_user.id,)

    await session_service.end_chat_session(
        session_id_str=session_id,
        ended_reason="user_left",
        fallback_tg_ids=fallback_ids,
    )

    # Double-ensure both users are IDLE immediately
    await state_manager.force_idle(telegram_user.id)
    if partner_id:
        await state_manager.force_idle(partner_id)

    # Notify partner
    if partner_id and event.bot:
        try:
            partner_card = (
                "🛑 <b>CONVERSATION ENDED</b>\n"
                "───────────────────────────────\n"
                "Your partner has left the chat session.\n\n"
                "• Status: ⚪️ <code>IDLE</code>\n"
                "───────────────────────────────\n"
                "👇 <i>Ready for another chat?</i>"
            )
            await event.bot.send_message(
                chat_id=partner_id,
                text=partner_card,
                reply_markup=idle_search_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug("Could not notify partner %s of /end: %s", partner_id, e)

    farewell_card = (
        "🛑 <b>CONVERSATION CLOSED</b>\n"
        "───────────────────────────────\n"
        "You have left the conversation. All temporary routing is cleared.\n\n"
        "• Status: ⚪️ <code>IDLE</code>\n"
        "───────────────────────────────\n"
        "👇 <i>Tap below whenever you want to chat again:</i>"
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer("Conversation ended.")
        if event.message:
            await event.message.answer(farewell_card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
    else:
        await event.answer(farewell_card, reply_markup=idle_search_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# /block command & callback — block partner & end chat
# ──────────────────────────────────────────────

@router.message(Command("block"))
@router.callback_query(F.data == "chat_block")
async def handle_block(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    user_match_state: UserStateData | None = None,
) -> None:
    """Block the current chat partner and immediately terminate the session."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    await state.clear()
    current_state = user_match_state or await state_manager.get_state(telegram_user.id)

    if not current_state.is_chatting or not current_state.session_id or not current_state.partner_id:
        card = (
            "ℹ️ <b>NO ACTIVE CONVERSATION</b>\n"
            "───────────────────────────────\n"
            "You are not in a conversation to block anyone.\n\n"
            "• Status: ⚪️ <code>IDLE</code>\n"
            "───────────────────────────────"
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        else:
            await event.answer(card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
        return

    partner_id = current_state.partner_id
    session_id = current_state.session_id or ""

    fallback_ids = (telegram_user.id, partner_id) if partner_id else (telegram_user.id,)

    # 1. Record block in database
    await moderation_service.block_by_telegram_ids(
        blocker_telegram_id=telegram_user.id,
        blocked_telegram_id=partner_id,
        reason="user_command",
    )

    # 2. Terminate chat session with guaranteed Redis teardown
    await session_service.end_chat_session(
        session_id_str=session_id,
        ended_reason="blocked",
        fallback_tg_ids=fallback_ids,
    )

    # Double-ensure both users are IDLE immediately
    await state_manager.force_idle(telegram_user.id)
    if partner_id:
        await state_manager.force_idle(partner_id)

    # 3. Notify partner neutrally
    if event.bot:
        try:
            partner_card = (
                "🛑 <b>CONVERSATION ENDED</b>\n"
                "───────────────────────────────\n"
                "The chat session has ended.\n\n"
                "• Status: ⚪️ <code>IDLE</code>\n"
                "───────────────────────────────\n"
                "👇 <i>Find someone new below:</i>"
            )
            await event.bot.send_message(
                chat_id=partner_id,
                text=partner_card,
                reply_markup=idle_search_keyboard(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug("Could not notify blocked user %s: %s", partner_id, e)

    block_card = (
        "🚫 <b>USER BLOCKED & DISCONNECTED</b>\n"
        "───────────────────────────────\n"
        "This partner has been permanently added to your blocklist.\n\n"
        "🔒 <b>Safety Guarantee:</b> You two will never be matched again.\n"
        "• Status: ⚪️ <code>IDLE</code>\n"
        "───────────────────────────────\n"
        "👇 <i>Find someone new below:</i>"
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer("User blocked.")
        if event.message:
            await event.message.answer(block_card, reply_markup=idle_search_keyboard(), parse_mode="HTML")
    else:
        await event.answer(block_card, reply_markup=idle_search_keyboard(), parse_mode="HTML")



# ──────────────────────────────────────────────
# Anonymous Message Relay (Text, Photo, Voice, Media)
# ──────────────────────────────────────────────

@router.message(~F.text.startswith("/"))
async def relay_chat_message(
    message: types.Message,
    user_match_state: UserStateData | None = None,
) -> None:
    """
    Relay an incoming non-command message to the user's active chat partner.

    Security Invariant: The recipient is derived strictly from the active session.
    """
    telegram_user = message.from_user
    if telegram_user is None or message.bot is None:
        return

    current_state = user_match_state or await state_manager.get_state(telegram_user.id)

    # If user is in active chat, relay the message to partner
    if current_state.is_chatting:
        partner_id = await session_service.get_active_session_partner(telegram_user.id)
        if not partner_id:
            await message.answer("⚠️ Your chat session has expired. Use /search to find a partner.")
            await state_manager.force_idle(telegram_user.id)
            return

        # Atomic sliding-window rate limit check (rl:msg:{user_id})
        rl = await rate_limiter.check_chat_rate_limit(telegram_user.id)
        if not rl.allowed:
            await message.answer(
                f"⚠️ <b>Slow down!</b> You're sending messages too fast.\n"
                f"Please wait <b>{rl.retry_after:.1f}s</b> before sending another message.",
                parse_mode="HTML",
            )
            return

        # Content Moderation: block unsolicited usernames & external links
        content_to_check = message.text or message.caption
        if content_to_check:
            mod_res = content_moderator.moderate_text(content_to_check)
            if not mod_res.is_allowed:
                await message.answer(
                    ContentModerationService.DEFAULT_BLOCKED_MESSAGE,
                    parse_mode="HTML",
                )
                return

        try:
            # Relay text


            if message.text:
                await message.bot.send_message(chat_id=partner_id, text=message.text)
            # Relay photo
            elif message.photo:
                await message.bot.send_photo(
                    chat_id=partner_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption,
                )
            # Relay voice
            elif message.voice:
                await message.bot.send_voice(
                    chat_id=partner_id,
                    voice=message.voice.file_id,
                    caption=message.caption,
                )
            # Relay audio
            elif message.audio:
                await message.bot.send_audio(
                    chat_id=partner_id,
                    audio=message.audio.file_id,
                    caption=message.caption,
                )
            # Relay video
            elif message.video:
                await message.bot.send_video(
                    chat_id=partner_id,
                    video=message.video.file_id,
                    caption=message.caption,
                )
            # Relay animation / gif
            elif message.animation:
                await message.bot.send_animation(
                    chat_id=partner_id,
                    animation=message.animation.file_id,
                    caption=message.caption,
                )
            # Relay sticker
            elif message.sticker:
                await message.bot.send_sticker(
                    chat_id=partner_id,
                    sticker=message.sticker.file_id,
                )
            # Relay video note (round circle video)
            elif message.video_note:
                await message.bot.send_video_note(
                    chat_id=partner_id,
                    video_note=message.video_note.file_id,
                )
            else:
                await message.answer("⚠️ This message format cannot be relayed.")

        except Exception as e:
            logger.warning("Failed to relay message from %s to partner %s: %s", telegram_user.id, partner_id, e)
            await message.answer("⚠️ Could not deliver message. Your partner may have disconnected.")

    elif current_state.is_searching:
        await message.answer(
            "🔎 <b>You are in the matchmaking queue!</b>\n\n"
            "Please wait while we connect you to a partner.\n"
            "Use <code>/stop</code> to cancel search.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "💬 <b>You are not currently in a chat.</b>\n\n"
            "Use <code>/search</code> to find someone to talk to!",
            parse_mode="HTML",
        )

