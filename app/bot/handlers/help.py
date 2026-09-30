"""
/help command handler — User guide and command overview.
"""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import help_keyboard

router = Router(name="help")


HELP_CARD_HTML = (
    "✨ <b>GUPSHUP • USER GUIDE & COMMANDS</b> ✨\n"
    "───────────────────────────────\n"
    "Connect instantly and anonymously with compatible partners across the world.\n\n"
    "🎯 <b>MATCHMAKING CONTROLS</b>\n"
    "• <code>/search</code> — Enter queue to find a chat partner\n"
    "• <code>/stop</code> — Leave queue & cancel search\n\n"
    "💬 <b>IN-CHAT CONTROLS</b>\n"
    "• <code>/next</code> — Instantly skip to your next match\n"
    "• <code>/end</code> — Gracefully disconnect from current chat\n"
    "• <code>/block</code> — Permanently block partner & disconnect\n\n"
    "⚙️ <b>PROFILE & PREFERENCES</b>\n"
    "• <code>/profile</code> — View and customize your public bio\n"
    "• <code>/settings</code> — Configure age, gender & language filters\n"
    "• <code>/start</code> — Open the main menu & status dashboard\n\n"
    "🛡 <b>PRIVACY & SAFETY</b>\n"
    "• Your Telegram handle and phone number are <b>never shared</b>.\n"
    "• All messages are relayed completely anonymously.\n"
    "• Blocked users can never be rematched with you.\n"
    "───────────────────────────────\n"
    "👇 <i>Choose an action below to get started:</i>"
)


@router.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext) -> None:
    """Display the structured user guide and command index."""
    await state.clear()
    await message.answer(
        HELP_CARD_HTML,
        reply_markup=help_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "nav_help")
async def on_nav_help(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle navigation to help screen via inline button."""
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            HELP_CARD_HTML,
            reply_markup=help_keyboard(),
            parse_mode="HTML",
        )
