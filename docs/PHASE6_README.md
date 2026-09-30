# Phase 6: Profile Editing & Search Preferences

## What This Phase Does

Phase 6 introduces two critical user-facing systems that bridge user onboarding with the matchmaking engine:

1. **Profile Editing (`/edit`)**: Allows users to view and update their existing identity attributes at any time without having to re-register. Users can selectively change a single field (gender, age range, language, or bio) in place, clear their bio, or choose to restart the full onboarding questionnaire from scratch.

2. **Search Preferences Management (`/settings`)**: A dedicated configuration center where users define *who they want to meet*. While `/edit` answers *"Who am I?"*, `/settings` answers *"Who do I want to be matched with?"*. Users can configure preferred partner gender (including an "Any" wildcard), preferred partner age range, and preferred conversation language.

3. **Persistent Preference Storage**: Creates the `search_preferences` database table with foreign-key coupling to the user identity. It uses lazy default initialization so every registered user always has a valid set of matching criteria ready for the matchmaking queue (Phase 9).

---

## Why It's Built This Way

### Why Separate Search Preferences from the Profile?

A common anti-pattern in early chatbot architectures is storing both personal attributes and partner criteria in a single `profiles` or `users` table. We deliberately decoupled them into two separate entities:

- **Conceptual Independence ("Who I Am" vs "Who I Want")**: A user's gender and age are personal demographic properties. Their partner preferences are search parameters. Mixing them leads to bloated tables and confusing query logic.
- **Asymmetric Matching**: Matchmaking is a two-way constraint problem. User A's profile must satisfy User B's search preferences, and simultaneously User B's profile must satisfy User A's search preferences. Decoupling the data models allows the query planner to index and filter profile attributes against preference tables independently without lock contention.
- **Independent Lifecycles**: A user rarely changes their gender or language, but they may frequently adjust their search filters (e.g., widening their age bracket when queue times are slow, or toggling between specific languages). Separating them isolates cache invalidations and reduces unnecessary writes to the profile table.

### Why Support Both Single-Field Edits and Full Redo?

- **Frictionless Corrections**: If a user realizes they made a typo in their bio or selected the wrong age bracket during initial onboarding, forcing them to answer all onboarding questions again creates high friction and abandonment. A single-field edit allows them to fix the mistake in two taps.
- **Complete Re-onboarding Option**: If a user's life circumstances change or they want a clean slate, a "Redo full profile" button lets them step through the guided onboarding wizard with clear linear guidance.

### Why Interactive Menus Instead of Linear Questionnaires for Settings?

Unlike onboarding—which is a one-time sequential funnel where the bot must collect all required fields before granting access—settings configuration is non-linear:

- Users typically open `/settings` to change **one specific dial** (e.g., switch preferred gender from "Female" to "Any").
- An interactive dashboard displaying the current configuration alongside targeted sub-menus allows users to inspect their settings, tap the specific item they want to change, see instant confirmation, and return to the main dashboard without tedious sequential prompts.

### Why Lazy Initialization (`get_or_create`)?

Instead of requiring explicit setup screens during onboarding for search preferences, the system lazily generates default preferences on first access or matchmaking entry:
- Default partner gender: `any` (maximum match availability)
- Default partner age range: `18` to `99` (broadest pool)
- Default partner language: `any` (inclusive)
- Default matchmaking state: `active`

This zero-friction approach allows new users to jump straight into searching immediately after completing their basic profile in Phase 5, while giving power users full control via `/settings` whenever they choose.

---

## How It Works (Implementation Architecture)

### 1. System Architecture

The implementation strictly maintains our 3-tier modular architecture:

```
┌────────────────────────────────────────────────────────┐
│                   Telegram User                        │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                     Bot Layer                          │
│  - /edit command handler                               │
│  - /settings command handler                           │
│  - Inline keyboards (menu dashboards & sub-options)    │
│  - FSM State Machine (EditStates, SettingsStates)      │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                    Domain Layer                        │
│  - ProfileService (partial updates, validation)        │
│  - PreferencesService (defaults, partial updates)      │
│  - Session lifecycle management (commit/rollback)      │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                  Persistence Layer                     │
│  - ProfileRepository & PreferencesRepository           │
│  - PostgreSQL tables: profiles, search_preferences     │
│  - Foreign Keys with ON DELETE CASCADE                 │
└────────────────────────────────────────────────────────┘
```

### 2. State Machine Flows

#### Profile Edit Flow (`/edit`)

```
[/edit Command]
       │
       ▼
[Check Profile Exists?]
   ├── No  ──► "Please complete onboarding with /start"
   └── Yes ──► [Display Current Profile Card + Edit Menu]
                    │
                    ├── Tap "Gender"   ──► [Gender Submenu] ──► [Tap Option] ──► (DB Updated) ──► [Return to Card]
                    ├── Tap "Age"      ──► [Age Submenu]    ──► [Tap Option] ──► (DB Updated) ──► [Return to Card]
                    ├── Tap "Language" ──► [Lang Submenu]   ──► [Tap Option] ──► (DB Updated) ──► [Return to Card]
                    ├── Tap "Bio"      ──► [Enter FSM: waiting_for_bio]
                    │                           ├── Send text (<=500 chars) ──► (DB Updated) ──► [Return to Card]
                    │                           ├── Tap "Clear Bio"        ──► (Bio = Null)  ──► [Return to Card]
                    │                           └── Tap "Back"             ──► [Cancel / Return to Card]
                    ├── Tap "Redo All" ──► [Enter OnboardingStates.waiting_for_gender] (Full Funnel)
                    └── Tap "Close"    ──► [Clear State & Dismiss Menu]
```

#### Settings Flow (`/settings`)

```
[/settings Command]
       │
       ▼
[Check Profile Exists?]
   ├── No  ──► "Please complete profile first with /start"
   └── Yes ──► [Display Current Preferences Card + Settings Menu]
                    │
                    ├── Tap "Preferred Gender"   ──► [Pref Gender Menu] ──► [Select] ──► (DB Updated) ──► [Refresh Menu]
                    ├── Tap "Preferred Age"      ──► [Pref Age Menu]    ──► [Select] ──► (DB Updated) ──► [Refresh Menu]
                    ├── Tap "Preferred Language" ──► [Pref Lang Menu]   ──► [Select] ──► (DB Updated) ──► [Refresh Menu]
                    ├── Tap "Back"               ──► [Return to Main Settings Menu]
                    └── Tap "Close"              ──► [Clear State & Close]
```

### 3. Database Schema: `search_preferences`

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| `id` | UUID | No | `uuid_generate_v4()` | Primary key |
| `user_id` | UUID | No | - | Foreign key → `users.id` (`ON DELETE CASCADE`, Unique, Indexed) |
| `preferred_gender` | VARCHAR(20) | No | `'any'` | `'any'`, `'male'`, `'female'`, `'other'` |
| `preferred_age_min` | INTEGER | No | `18` | Minimum partner age (inclusive) |
| `preferred_age_max` | INTEGER | No | `99` | Maximum partner age (inclusive) |
| `preferred_language` | VARCHAR(10) | No | `'any'` | `'any'`, `'en'`, `'hi'`, `'es'`, `'fr'`, etc. |
| `is_active` | BOOLEAN | No | `TRUE` | Global matchmaking toggle for the user |
| `created_at` | TIMESTAMPTZ | No | `now()` | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | No | `now()` | Last modification timestamp |

### 4. Integrity, Validation & Privacy Rules

- **Strict Anonymity**: Neither the `/edit` menu nor the `/settings` menu ever reveals the user's Telegram username, phone number, or Telegram user ID.
- **Bio Length Restraint**: Profile bios are strictly bounded to 500 characters to prevent spam payloads or message fragmentation in chat UI.
- **Age Sanity Constraints**: Age preferences enforce bounds between 18 and 99 years old.
- **Cascading Teardown**: If a user account is deleted, the database automatically cascades deletion to both the `profiles` and `search_preferences` tables, preventing orphaned records.
- **Atomic Operations**: Profile and preference updates are executed inside isolated database transactions; any connection failure triggers an immediate rollback to avoid partial or corrupted states.

---

## What Comes Next

With the completion of Phase 6:
- User identity (`users`) is live.
- User profile (`profiles`) is live and fully editable via `/edit`.
- Matchmaking criteria (`search_preferences`) are live and configurable via `/settings`.

We are now ready for **Phase 7: Redis Connection & State Machine**, which introduces Redis for high-speed volatile state management, user presence tracking, and matchmaking queue buffers.
