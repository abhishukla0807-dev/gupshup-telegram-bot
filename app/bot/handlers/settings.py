"""
/settings command handler — manage search preferences.

This is separate from /edit (which changes the user's own profile).
/settings controls what kind of partner the user wants to be matched with:
  - Preferred gender (any, male, female, other)
  - Preferred age range (any, 18-24, 25-34, 35-44, 45+)
  - Preferred language (any, en, hi, es, fr, de, ru)

The flow uses an interactive menu: the user picks a field, changes it,
and returns to the menu. They can change multiple fields in one session.
"""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import (
    pref_age_keyboard,
    pref_gender_keyboard,
    pref_language_keyboard,
    settings_menu_keyboard,
)
from app.bot.states import SettingsStates
from app.core.users.preferences_service import PreferencesService
from app.core.users.profile_service import ProfileService

router = Router(name="settings")
preferences_service = PreferencesService()
profile_service = ProfileService()

# Display labels
GENDER_LABELS = {"any": "🌈 Any", "male": "👨 Male", "female": "👩 Female", "other": "🧑 Other"}
LANGUAGE_LABELS = {
    "any": "🌈 Any",
    "en": "🇬🇧 English",
    "hi": "🇮🇳 Hindi",
    "es": "🇪🇸 Spanish",
    "fr": "🇫🇷 French",
    "de": "🇩🇪 German",
    "ru": "🇷🇺 Russian",
}


def _format_age_range(age_min: int, age_max: int) -> str:
    """Human-readable age range."""
    if age_min == 18 and age_max == 99:
        return "🌈 Any age"
    if age_max == 99:
        return f"{age_min}+"
    return f"{age_min}-{age_max}"


async def _show_settings_menu(
    message: types.Message,
    telegram_id: int,
    state: FSMContext,
    edit_existing: bool = False,
) -> None:
    """Display the settings menu with current preference values."""
    prefs = await preferences_service.get_preferences(telegram_id)

    gender_label = GENDER_LABELS.get(prefs.preferred_gender, prefs.preferred_gender)
    age_label = _format_age_range(prefs.preferred_age_min, prefs.preferred_age_max)
    lang_label = LANGUAGE_LABELS.get(prefs.preferred_language, prefs.preferred_language)

    text = (
        "⚙️ <b>MATCHING & SEARCH PREFERENCES</b>\n"
        "───────────────────────────────\n"
        "Configure constraints for the matchmaking engine:\n\n"
        f"• <b>Preferred Gender:</b> <code>{gender_label}</code>\n"
        f"• <b>Preferred Age:</b> <code>{age_label}</code>\n"
        f"• <b>Preferred Language:</b> <code>{lang_label}</code>\n\n"
        "💡 <i>Mutual hard constraints are enforced first before calculating compatibility scores.</i>\n"
        "───────────────────────────────\n"
        "👇 <i>Tap a setting below to adjust:</i>"
    )

    await state.set_state(SettingsStates.viewing_menu)

    if edit_existing:
        await message.edit_text(text, reply_markup=settings_menu_keyboard(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=settings_menu_keyboard(), parse_mode="HTML")


# ──────────────────────────────────────────────
# /settings command & navigation callback
# ──────────────────────────────────────────────

@router.message(Command("settings"))
@router.callback_query(F.data == "nav_settings")
async def cmd_settings(event: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    """Show the search preferences menu."""
    telegram_user = event.from_user
    if telegram_user is None:
        return

    has_profile = await profile_service.has_complete_profile(telegram_user.id)
    if not has_profile:
        msg = (
            "⚠️ <b>PROFILE NOT FOUND</b>\n"
            "───────────────────────────────\n"
            "You need to complete your profile setup first.\n"
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
            await _show_settings_menu(event.message, telegram_user.id, state, edit_existing=True)
    else:
        await _show_settings_menu(event, telegram_user.id, state, edit_existing=False)



# ──────────────────────────────────────────────
# Navigate to gender preference
# ──────────────────────────────────────────────

@router.callback_query(SettingsStates.viewing_menu, F.data == "pref_menu_gender")
async def settings_gender(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show preferred gender options."""
    await state.set_state(SettingsStates.waiting_for_pref_gender)
    await callback.message.edit_text(
        "👤 Select your **preferred partner gender**:",
        reply_markup=pref_gender_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(SettingsStates.waiting_for_pref_gender, F.data.startswith("pref_gender_"))
async def on_pref_gender_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User selected a preferred gender."""
    gender = callback.data.replace("pref_gender_", "")

    await preferences_service.update_preferences(
        telegram_id=callback.from_user.id,
        preferred_gender=gender,
    )

    await callback.answer(f"✅ Preferred gender set to: {GENDER_LABELS.get(gender, gender)}")
    await _show_settings_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Navigate to age preference
# ──────────────────────────────────────────────

@router.callback_query(SettingsStates.viewing_menu, F.data == "pref_menu_age")
async def settings_age(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show preferred age range options."""
    await state.set_state(SettingsStates.waiting_for_pref_age)
    await callback.message.edit_text(
        "📅 Select your **preferred partner age range**:",
        reply_markup=pref_age_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(SettingsStates.waiting_for_pref_age, F.data.startswith("pref_age_"))
async def on_pref_age_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User selected a preferred age range."""
    raw = callback.data.replace("pref_age_", "")

    if raw == "any":
        age_min, age_max = 18, 99
    else:
        parts = raw.split("_")
        age_min, age_max = int(parts[0]), int(parts[1])

    await preferences_service.update_preferences(
        telegram_id=callback.from_user.id,
        preferred_age_min=age_min,
        preferred_age_max=age_max,
    )

    label = _format_age_range(age_min, age_max)
    await callback.answer(f"✅ Preferred age set to: {label}")
    await _show_settings_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Navigate to language preference
# ──────────────────────────────────────────────

@router.callback_query(SettingsStates.viewing_menu, F.data == "pref_menu_language")
async def settings_language(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Show preferred language options."""
    await state.set_state(SettingsStates.waiting_for_pref_language)
    await callback.message.edit_text(
        "🌐 Select your **preferred partner language**:",
        reply_markup=pref_language_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(SettingsStates.waiting_for_pref_language, F.data.startswith("pref_lang_"))
async def on_pref_lang_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User selected a preferred language."""
    language = callback.data.replace("pref_lang_", "")

    await preferences_service.update_preferences(
        telegram_id=callback.from_user.id,
        preferred_language=language,
    )

    label = LANGUAGE_LABELS.get(language, language)
    await callback.answer(f"✅ Preferred language set to: {label}")
    await _show_settings_menu(callback.message, callback.from_user.id, state, edit_existing=True)


# ──────────────────────────────────────────────
# Back to menu / Close
# ──────────────────────────────────────────────

@router.callback_query(F.data == "pref_back_menu")
async def settings_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Return to the settings menu from a sub-screen."""
    await _show_settings_menu(callback.message, callback.from_user.id, state, edit_existing=True)
    await callback.answer()


@router.callback_query(F.data == "pref_close")
async def settings_close(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Close the settings menu."""
    await state.clear()
    await callback.message.edit_text("✅ Settings saved and closed.")
    await callback.answer()
