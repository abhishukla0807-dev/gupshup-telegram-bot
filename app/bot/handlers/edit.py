"""
/edit command handler.

Lets users update their own profile after initial onboarding.
Two modes:
  - Pick a single field to change (gender, age, language, or bio)
  - Redo the full onboarding flow from scratch

Single-field edits happen inline and return to the edit menu.
Full redo re-enters the Phase 5 OnboardingStates FSM.
"""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import (
    edit_age_keyboard,
    edit_bio_keyboard,
    edit_gender_keyboard,
    edit_language_keyboard,
    edit_profile_keyboard,
    gender_keyboard,
)
from app.bot.states import EditStates, OnboardingStates
from app.core.users.profile_service import ProfileService

router = Router(name="edit")
profile_service = ProfileService()

# Display labels for languages
LANGUAGE_LABELS = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 Hindi",
    "es": "🇪🇸 Spanish",
    "fr": "🇫🇷 French",
    "de": "🇩🇪 German",
    "ru": "🇷🇺 Russian",
}


def _format_age_label(age: int) -> str:
    """Format age midpoint back to human range."""
    if age == 21:
        return "18-24"
    elif age == 30:
        return "25-34"
    elif age == 40:
        return "35-44"
    elif age == 50:
        return "45+"
    return str(age)


import html

GENDER_ICONS = {"male": "👨", "female": "👩", "other": "🧑"}

async def _show_edit_menu(
    message: types.Message,
    telegram_id: int,
    state: FSMContext,
    edit_existing: bool = False,
) -> None:
    """Render the profile edit menu showing current values."""
    await state.clear()
    profile = await profile_service.get_profile(telegram_id)
    if profile is None:
        text = (
            "⚠️ <b>PROFILE NOT FOUND</b>\n"
            "───────────────────────────────\n"
            "You haven't set up your profile yet.\n"
            "Use <code>/start</code> to begin onboarding!"
        )
        if edit_existing:
            await message.edit_text(text, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML")
        return

    lang_label = LANGUAGE_LABELS.get(profile.language, profile.language)
    age_label = _format_age_label(profile.age)
    gender_icon = GENDER_ICONS.get(profile.gender, "👤")
    raw_bio = profile.bio or "Not set"
    safe_bio = html.escape(raw_bio)

    text = (
        "👤 <b>YOUR ANONYMOUS PROFILE</b>\n"
        "───────────────────────────────\n"
        "This is what prospective partners see when matched:\n\n"
        f"• <b>Gender:</b> {gender_icon} <code>{profile.gender.capitalize()}</code>\n"
        f"• <b>Age Bracket:</b> 📅 <code>{age_label}</code>\n"
        f"• <b>Language:</b> 🌐 <code>{lang_label}</code>\n"
        f"• <b>Bio:</b> 💬 <i>{safe_bio}</i>\n\n"
        "───────────────────────────────\n"
        "👇 <i>Tap a button below to update any field:</i>"
    )

    if edit_existing:
        await message.edit_text(text, reply_markup=edit_profile_keyboard(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=edit_profile_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# /edit and /profile commands & navigation callback
# ──────────────────────────────────────────────

@router.message(Command(commands=["edit", "profile"]))
@router.callback_query(F.data == "nav_profile")
async def cmd_edit(event: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    """Show the profile edit menu."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    has_profile = await profile_service.has_complete_profile(telegram_user.id)
    if not has_profile:
        msg = (
            "⚠️ <b>PROFILE NOT FOUND</b>\n"
            "───────────────────────────────\n"
            "You haven't set up your profile yet.\n"
            "Use <code>/start</code> to begin onboarding!"
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(msg, parse_mode="HTML")
        else:
            await event.answer(msg, parse_mode="HTML")
        return

    if isinstance(event, types.CallbackQuery):
        await event.answer()
        if event.message:
            await _show_edit_menu(event.message, telegram_user.id, state, edit_existing=True)
    else:
        await _show_edit_menu(event, telegram_user.id, state, edit_existing=False)



# ──────────────────────────────────────────────
# Single-field edit: Gender
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_field_gender")
async def edit_gender(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show gender selection for editing."""
    await state.clear()
    await callback.message.edit_text(
        "👤 Select your new gender:",
        reply_markup=edit_gender_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_gender_"))
async def on_edit_gender_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle gender selection and update profile."""
    gender = callback.data.replace("edit_gender_", "")
    await profile_service.update_profile(
        telegram_id=callback.from_user.id,
        gender=gender,
    )
    await callback.answer(f"✅ Gender updated to {gender.capitalize()}!")
    await _show_edit_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Single-field edit: Age
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_field_age")
async def edit_age(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show age selection for editing."""
    await state.clear()
    await callback.message.edit_text(
        "📅 Select your new age range:",
        reply_markup=edit_age_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_age_"))
async def on_edit_age_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle age range selection and update profile."""
    age = int(callback.data.replace("edit_age_", ""))
    await profile_service.update_profile(
        telegram_id=callback.from_user.id,
        age=age,
    )
    label = _format_age_label(age)
    await callback.answer(f"✅ Age range updated to {label}!")
    await _show_edit_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Single-field edit: Language
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_field_language")
async def edit_language(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show language selection for editing."""
    await state.clear()
    await callback.message.edit_text(
        "🌐 Select your new language:",
        reply_markup=edit_language_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_lang_"))
async def on_edit_language_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle language selection and update profile."""
    language = callback.data.replace("edit_lang_", "")
    await profile_service.update_profile(
        telegram_id=callback.from_user.id,
        language=language,
    )
    label = LANGUAGE_LABELS.get(language, language)
    await callback.answer(f"✅ Language updated to {label}!")
    await _show_edit_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Single-field edit: Bio
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_field_bio")
async def edit_bio(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Prompt for new bio text."""
    await state.set_state(EditStates.waiting_for_bio)
    await callback.message.edit_text(
        "📝 Type your new bio below (max 500 characters):\n\n"
        "Or tap **Clear Bio** to remove your bio entirely.",
        reply_markup=edit_bio_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(EditStates.waiting_for_bio, F.text)
async def on_bio_text_edited(message: types.Message, state: FSMContext) -> None:
    """Handle user typing new bio."""
    telegram_user = message.from_user
    if telegram_user is None:
        return

    bio = message.text.strip()
    if len(bio) > 500:
        await message.answer("⚠️ Bio is too long (max 500 characters). Please send a shorter bio:")
        return

    await profile_service.update_profile(
        telegram_id=telegram_user.id,
        bio=bio,
    )
    await state.clear()
    await message.answer("✅ Bio updated successfully!")
    await _show_edit_menu(message, telegram_user.id, state, edit_existing=False)


@router.callback_query(F.data == "edit_bio_clear")
async def on_bio_cleared(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle user choosing to clear bio."""
    await profile_service.update_profile(
        telegram_id=callback.from_user.id,
        bio=None,
    )
    await state.clear()
    await callback.answer("✅ Bio cleared!")
    await _show_edit_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Redo full profile
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_redo_all")
async def edit_redo_all(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Re-enter the full onboarding FSM from the beginning."""
    await state.clear()
    await state.set_state(OnboardingStates.waiting_for_gender)
    await callback.message.edit_text(
        "🔄 Let's redo your profile from scratch!\n\n"
        "🧑 Select your gender:",
        reply_markup=gender_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Back to Edit Menu
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_back_menu")
async def edit_back_to_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Return to the main edit menu."""
    await _show_edit_menu(callback.message, callback.from_user.id, state, edit_existing=True)
    await callback.answer()


# ──────────────────────────────────────────────
# Close the edit menu
# ──────────────────────────────────────────────

@router.callback_query(F.data == "edit_close")
async def edit_close(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Close the edit menu and clear any FSM state."""
    await state.clear()
    await callback.message.edit_text("✅ Profile editing closed.")
    await callback.answer()
