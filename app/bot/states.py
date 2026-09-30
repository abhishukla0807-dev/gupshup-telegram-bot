"""
FSM states for bot conversation flows.

Aiogram's Finite State Machine tracks where each user is in a
multi-step conversation. Each state represents one question the
bot is waiting for an answer to.
"""
from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """States for the new-user onboarding flow (Phase 5).

    Flow: gender → age → language → bio (optional) → done
    """

    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_language = State()
    waiting_for_bio = State()


class SettingsStates(StatesGroup):
    """States for the /settings search preferences flow (Phase 6).

    This is a separate FSM group from onboarding because:
    - It uses different callback_data prefixes (pref_ vs onb_)
    - It updates a different table (search_preferences vs profiles)
    - It can be entered/exited independently of onboarding

    Flow: menu → (pick field to change) → update → back to menu
    """

    viewing_menu = State()
    waiting_for_pref_gender = State()
    waiting_for_pref_age = State()
    waiting_for_pref_language = State()


class EditStates(StatesGroup):
    """States for the /edit profile editing flow (Phase 6).

    Used when editing fields that require text input (e.g. bio).
    """

    waiting_for_bio = State()

