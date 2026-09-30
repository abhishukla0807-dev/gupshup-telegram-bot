"""
/start command handler.

This is the entry point for every user who opens the bot for the first
time (or returns later). It creates or fetches the user from the database
and responds with the appropriate welcome message.

For new users, it immediately kicks off the onboarding FSM flow.
For returning users without a profile, it also kicks off onboarding.
"""
from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import gender_keyboard
from app.bot.states import OnboardingStates
from app.core.users.service import UserService
from app.core.users.profile_service import ProfileService

router = Router(name="start")
user_service = UserService()
profile_service = ProfileService()


import html

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """Handle the /start command."""
    telegram_user = message.from_user
    if telegram_user is None:
        return

    user, is_new = await user_service.register_or_fetch(
        telegram_id=telegram_user.id,
        first_name=telegram_user.first_name or "Anonymous",
        username=telegram_user.username,
    )
    safe_name = html.escape(telegram_user.first_name or "Friend")

    if is_new:
        # New user → welcome onboarding card
        await state.clear()
        await message.answer(
            f"✨ <b>WELCOME TO GUPSHUP</b> ✨\n"
            "───────────────────────────────\n"
            f"👋 Hello, <b>{safe_name}</b>!\n\n"
            "I connect you with random people for 1-on-1 conversations.\n"
            "Your identity and personal information remain <b>100% private</b>.\n\n"
            "📋 <b>QUICK SETUP • STEP 1 OF 3</b>\n"
            "Let's configure your profile so our engine can pair you accurately!\n"
            "───────────────────────────────\n"
            "👇 <b>Select your gender:</b>",
            reply_markup=gender_keyboard(),
            parse_mode="HTML",
        )
        await state.set_state(OnboardingStates.waiting_for_gender)
    else:
        # Returning user — check if they have a complete profile
        has_profile = await profile_service.has_complete_profile(telegram_user.id)

        if has_profile:
            from app.bot.keyboards import welcome_keyboard

            await state.clear()
            await message.answer(
                f"✨ <b>GUPSHUP • ANONYMOUS CHAT</b> ✨\n"
                "───────────────────────────────\n"
                f"👋 <b>Welcome back, {safe_name}!</b>\n\n"
                "Your private matchmaking portal is ready. Find a conversation partner or adjust your preferences below.\n\n"
                "📊 <b>STATUS OVERVIEW</b>\n"
                "• Match Status: ⚪️ <code>IDLE</code>\n"
                "• Relay Mode: 🔒 <code>100% ANONYMOUS</code>\n"
                "• Profile: ✅ <code>COMPLETE</code>\n\n"
                "⚡️ <b>QUICK CONTROLS</b>\n"
                "• <code>/search</code> — Enter matchmaking queue\n"
                "• <code>/profile</code> — View & update profile info\n"
                "• <code>/settings</code> — Filter gender, age & language\n"
                "• <code>/help</code> — Full commands index\n"
                "───────────────────────────────\n"
                "👇 <i>Choose an action below to get started:</i>",
                reply_markup=welcome_keyboard(),
                parse_mode="HTML",
            )
        else:
            # Returning user but never finished onboarding
            await state.clear()
            await message.answer(
                f"✨ <b>WELCOME BACK TO GUPSHUP</b> ✨\n"
                "───────────────────────────────\n"
                f"👋 Hello, <b>{safe_name}</b>!\n\n"
                "You haven't finished completing your profile yet.\n"
                "Please finish setup to begin searching for chat partners.\n"
                "───────────────────────────────\n"
                "👇 <b>Select your gender:</b>",
                reply_markup=gender_keyboard(),
                parse_mode="HTML",
            )
            await state.set_state(OnboardingStates.waiting_for_gender)

