"""
Inline keyboard builders for all bot flows.

Each function returns an InlineKeyboardMarkup that Telegram renders
as tappable buttons inside the chat. The callback_data string is what
our handler receives when the user taps a button.

Naming conventions for callback_data:
    onb_<step>_<value>   — onboarding flow (Phase 5)
    pref_<step>_<value>  — search preferences flow (Phase 6)
    edit_<action>        — profile edit actions (Phase 6)
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# ═══════════════════════════════════════════════
# Onboarding keyboards (Phase 5)
# ═══════════════════════════════════════════════

def gender_keyboard() -> InlineKeyboardMarkup:
    """Ask the user to select their gender."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Male", callback_data="onb_gender_male"),
                InlineKeyboardButton(text="👩 Female", callback_data="onb_gender_female"),
            ],
            [
                InlineKeyboardButton(text="🧑 Other", callback_data="onb_gender_other"),
            ],
        ]
    )


def age_keyboard() -> InlineKeyboardMarkup:
    """Ask the user to select their age range (we store the midpoint)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="18-24", callback_data="onb_age_21"),
                InlineKeyboardButton(text="25-34", callback_data="onb_age_30"),
            ],
            [
                InlineKeyboardButton(text="35-44", callback_data="onb_age_40"),
                InlineKeyboardButton(text="45+", callback_data="onb_age_50"),
            ],
        ]
    )


def language_keyboard() -> InlineKeyboardMarkup:
    """Ask the user to select their preferred chat language."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="onb_lang_en"),
                InlineKeyboardButton(text="🇮🇳 Hindi", callback_data="onb_lang_hi"),
            ],
            [
                InlineKeyboardButton(text="🇪🇸 Spanish", callback_data="onb_lang_es"),
                InlineKeyboardButton(text="🇫🇷 French", callback_data="onb_lang_fr"),
            ],
            [
                InlineKeyboardButton(text="🇩🇪 German", callback_data="onb_lang_de"),
                InlineKeyboardButton(text="🇷🇺 Russian", callback_data="onb_lang_ru"),
            ],
        ]
    )


def bio_skip_keyboard() -> InlineKeyboardMarkup:
    """Give the user an option to skip the bio step."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭️ Skip", callback_data="onb_bio_skip"),
            ],
        ]
    )


# ═══════════════════════════════════════════════
# Profile edit keyboard (Phase 6)
# ═══════════════════════════════════════════════

def edit_profile_keyboard() -> InlineKeyboardMarkup:
    """Main menu for /edit — pick which profile field to change."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Gender", callback_data="edit_field_gender"),
                InlineKeyboardButton(text="📅 Age", callback_data="edit_field_age"),
            ],
            [
                InlineKeyboardButton(text="🌐 Language", callback_data="edit_field_language"),
                InlineKeyboardButton(text="📝 Bio", callback_data="edit_field_bio"),
            ],
            [
                InlineKeyboardButton(text="🔄 Redo full profile", callback_data="edit_redo_all"),
            ],
            [
                InlineKeyboardButton(text="❌ Close", callback_data="edit_close"),
            ],
        ]
    )


def edit_gender_keyboard() -> InlineKeyboardMarkup:
    """Select new gender during /edit."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Male", callback_data="edit_gender_male"),
                InlineKeyboardButton(text="👩 Female", callback_data="edit_gender_female"),
            ],
            [
                InlineKeyboardButton(text="🧑 Other", callback_data="edit_gender_other"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="edit_back_menu"),
            ],
        ]
    )


def edit_age_keyboard() -> InlineKeyboardMarkup:
    """Select new age during /edit."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="18-24", callback_data="edit_age_21"),
                InlineKeyboardButton(text="25-34", callback_data="edit_age_30"),
            ],
            [
                InlineKeyboardButton(text="35-44", callback_data="edit_age_40"),
                InlineKeyboardButton(text="45+", callback_data="edit_age_50"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="edit_back_menu"),
            ],
        ]
    )


def edit_language_keyboard() -> InlineKeyboardMarkup:
    """Select new language during /edit."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="edit_lang_en"),
                InlineKeyboardButton(text="🇮🇳 Hindi", callback_data="edit_lang_hi"),
            ],
            [
                InlineKeyboardButton(text="🇪🇸 Spanish", callback_data="edit_lang_es"),
                InlineKeyboardButton(text="🇫🇷 French", callback_data="edit_lang_fr"),
            ],
            [
                InlineKeyboardButton(text="🇩🇪 German", callback_data="edit_lang_de"),
                InlineKeyboardButton(text="🇷🇺 Russian", callback_data="edit_lang_ru"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="edit_back_menu"),
            ],
        ]
    )


def edit_bio_keyboard() -> InlineKeyboardMarkup:
    """Options when editing bio."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ Clear Bio", callback_data="edit_bio_clear"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="edit_back_menu"),
            ],
        ]
    )



# ═══════════════════════════════════════════════
# Search preferences keyboards (Phase 6)
# ═══════════════════════════════════════════════

def settings_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu for /settings — pick which preference to change."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Preferred Gender", callback_data="pref_menu_gender"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Preferred Age Range", callback_data="pref_menu_age"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Preferred Language", callback_data="pref_menu_language"
                ),
            ],
            [
                InlineKeyboardButton(text="❌ Close", callback_data="pref_close"),
            ],
        ]
    )


def pref_gender_keyboard() -> InlineKeyboardMarkup:
    """Select preferred partner gender (includes 'Any')."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌈 Any", callback_data="pref_gender_any"),
            ],
            [
                InlineKeyboardButton(text="👨 Male", callback_data="pref_gender_male"),
                InlineKeyboardButton(text="👩 Female", callback_data="pref_gender_female"),
            ],
            [
                InlineKeyboardButton(text="🧑 Other", callback_data="pref_gender_other"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="pref_back_menu"),
            ],
        ]
    )


def pref_age_keyboard() -> InlineKeyboardMarkup:
    """Select preferred partner age range."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌈 Any age", callback_data="pref_age_any"),
            ],
            [
                InlineKeyboardButton(text="18-24", callback_data="pref_age_18_24"),
                InlineKeyboardButton(text="25-34", callback_data="pref_age_25_34"),
            ],
            [
                InlineKeyboardButton(text="35-44", callback_data="pref_age_35_44"),
                InlineKeyboardButton(text="45+", callback_data="pref_age_45_99"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="pref_back_menu"),
            ],
        ]
    )


def pref_language_keyboard() -> InlineKeyboardMarkup:
    """Select preferred partner language (includes 'Any')."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌈 Any language", callback_data="pref_lang_any"),
            ],
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="pref_lang_en"),
                InlineKeyboardButton(text="🇮🇳 Hindi", callback_data="pref_lang_hi"),
            ],
            [
                InlineKeyboardButton(text="🇪🇸 Spanish", callback_data="pref_lang_es"),
                InlineKeyboardButton(text="🇫🇷 French", callback_data="pref_lang_fr"),
            ],
            [
                InlineKeyboardButton(text="🇩🇪 German", callback_data="pref_lang_de"),
                InlineKeyboardButton(text="🇷🇺 Russian", callback_data="pref_lang_ru"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="pref_back_menu"),
            ],
        ]
    )


# ═══════════════════════════════════════════════
# Matchmaking Queue keyboard (Phase 8)
# ═══════════════════════════════════════════════

def search_waiting_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown while a user is waiting in the matchmaking queue."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Cancel Search", callback_data="search_cancel"),
            ],
        ]
    )


# ═══════════════════════════════════════════════
# Dashboard & Navigation keyboards
# ═══════════════════════════════════════════════

def welcome_keyboard() -> InlineKeyboardMarkup:
    """Primary dashboard keyboard displayed on /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Find a Partner", callback_data="start_search"),
            ],
            [
                InlineKeyboardButton(text="👤 My Profile", callback_data="nav_profile"),
                InlineKeyboardButton(text="⚙️ Preferences", callback_data="nav_settings"),
            ],
            [
                InlineKeyboardButton(text="📖 User Guide", callback_data="nav_help"),
            ],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    """Navigation keyboard for the /help overview."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Start Chatting", callback_data="start_search"),
                InlineKeyboardButton(text="⚙️ Preferences", callback_data="nav_settings"),
            ],
            [
                InlineKeyboardButton(text="👤 My Profile", callback_data="nav_profile"),
            ],
        ]
    )


def idle_search_keyboard() -> InlineKeyboardMarkup:
    """Prompt keyboard when user is idle or just finished/stopped a session."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Find a Partner", callback_data="start_search"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Preferences", callback_data="nav_settings"),
                InlineKeyboardButton(text="👤 My Profile", callback_data="nav_profile"),
            ],
        ]
    )


def chat_controls_keyboard() -> InlineKeyboardMarkup:
    """In-chat controls for active conversations."""
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


def search_timeout_keyboard() -> InlineKeyboardMarkup:
    """Prompt keyboard when matchmaking search times out after 1 minute."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Search Again", callback_data="search_again"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Preferences", callback_data="nav_settings"),
                InlineKeyboardButton(text="👤 My Profile", callback_data="nav_profile"),
            ],
        ]
    )



