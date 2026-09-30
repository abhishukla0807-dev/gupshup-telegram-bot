"""
Onboarding handler — FSM-driven conversation to collect profile data.

This handler guides new users through 4 steps:
  1. Select gender  (inline keyboard)
  2. Select age     (inline keyboard)
  3. Select language (inline keyboard)
  4. Write a bio    (free text or skip)

At each step, the FSM state advances. When the user completes all steps,
the profile is saved to the database and the user is ready to /search.

The flow can be triggered by:
  - /start (for new users, kicked off automatically)
  - /edit  (for returning users who want to redo onboarding)
"""
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import (
    age_keyboard,
    bio_skip_keyboard,
    gender_keyboard,
    language_keyboard,
)
from app.bot.states import OnboardingStates
from app.core.users.profile_service import ProfileService

router = Router(name="onboarding")
profile_service = ProfileService()


# ──────────────────────────────────────────────
# Step 1: Gender selection
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("onb_gender_"))
async def on_gender_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User tapped a gender button."""
    gender = callback.data.replace("onb_gender_", "")  # male | female | other

    # Store in FSM data for later
    await state.update_data(gender=gender)

    # Move to next step
    await state.set_state(OnboardingStates.waiting_for_age)

    # Acknowledge the button press and show next question
    await callback.message.edit_text(
        f"✅ Gender: **{gender.capitalize()}**\n\n"
        "📅 Now select your age range:",
        reply_markup=age_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Step 2: Age selection
# ──────────────────────────────────────────────

@router.callback_query(
    OnboardingStates.waiting_for_age,
    F.data.startswith("onb_age_"),
)
async def on_age_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User tapped an age range button."""
    age = int(callback.data.replace("onb_age_", ""))  # 21, 30, 40, 50

    await state.update_data(age=age)
    await state.set_state(OnboardingStates.waiting_for_language)

    await callback.message.edit_text(
        f"✅ Age range: **{age}**\n\n"
        "🌐 Select your preferred chat language:",
        reply_markup=language_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Step 3: Language selection
# ──────────────────────────────────────────────

LANGUAGE_LABELS = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 Hindi",
    "es": "🇪🇸 Spanish",
    "fr": "🇫🇷 French",
    "de": "🇩🇪 German",
    "ru": "🇷🇺 Russian",
}


@router.callback_query(
    OnboardingStates.waiting_for_language,
    F.data.startswith("onb_lang_"),
)
async def on_language_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User tapped a language button."""
    language = callback.data.replace("onb_lang_", "")  # en, hi, es, etc.
    label = LANGUAGE_LABELS.get(language, language)

    await state.update_data(language=language)
    await state.set_state(OnboardingStates.waiting_for_bio)

    await callback.message.edit_text(
        f"✅ Language: **{label}**\n\n"
        "📝 Write a short bio about yourself (1-2 sentences).\n"
        "This will be shown to your chat partner.\n\n"
        "_Or tap Skip to leave it blank._",
        reply_markup=bio_skip_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Step 4a: Bio — user types text
# ──────────────────────────────────────────────

@router.message(OnboardingStates.waiting_for_bio, F.text)
async def on_bio_text(message: types.Message, state: FSMContext) -> None:
    """User typed their bio as free text."""
    bio = message.text.strip()

    if len(bio) > 500:
        await message.answer("⚠️ Bio is too long (max 500 characters). Try again:")
        return

    await state.update_data(bio=bio)
    await _finish_onboarding(message, state)


# ──────────────────────────────────────────────
# Step 4b: Bio — user taps Skip
# ──────────────────────────────────────────────

@router.callback_query(
    OnboardingStates.waiting_for_bio,
    F.data == "onb_bio_skip",
)
async def on_bio_skipped(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User chose to skip the bio."""
    await state.update_data(bio=None)
    await callback.answer()
    await _finish_onboarding(callback.message, state, from_user=callback.from_user)


import html


# ──────────────────────────────────────────────
# Finalize: Save profile to database
# ──────────────────────────────────────────────

async def _finish_onboarding(
    message: types.Message,
    state: FSMContext,
    from_user: types.User | None = None,
) -> None:
    """Collect all FSM data, save the profile, and clear state."""
    data = await state.get_data()
    telegram_user = from_user or message.from_user

    if telegram_user is None:
        return

    # Save to database
    await profile_service.create_profile(
        telegram_id=telegram_user.id,
        gender=data["gender"],
        age=data["age"],
        language=data["language"],
        bio=data.get("bio"),
    )

    # Clear FSM state — user is now IDLE
    await state.clear()

    # Build summary
    gender_display = html.escape(data["gender"].capitalize())
    lang_display = html.escape(LANGUAGE_LABELS.get(data["language"], data["language"]))
    raw_bio = data.get("bio")
    bio_display = html.escape(raw_bio) if raw_bio else "<i>Not set</i>"

    await message.answer(
        "🎉 <b>Profile complete!</b> Here's your summary:\n\n"
        f"👤 <b>Gender:</b> {gender_display}\n"
        f"📅 <b>Age:</b> {data['age']}\n"
        f"🌐 <b>Language:</b> {lang_display}\n"
        f"📝 <b>Bio:</b> {bio_display}\n\n"
        "You're all set! Use <code>/search</code> to find a chat partner.",
        parse_mode="HTML",
    )

