# Phase 5: Profile + Onboarding

## What This Phase Does

Phase 5 gives users an **identity within the matchmaker** — not their Telegram
identity (that stays hidden), but the attributes that the matching engine will use
to pair them with compatible partners.

It does three things:

1. **Creates the `profiles` database table** — stores gender, age, language, and an
   optional bio for every user. This is deliberately separated from the `users` table
   because User is about *who you are* (identity/auth) while Profile is about *what
   you're looking for* (matchmaking data).

2. **Builds Telegram Inline Keyboards** — the tappable buttons that appear inside the
   chat. Instead of asking users to type "male" or "female" (error-prone, bad UX),
   we present clean button menus for gender, age range, and language.

3. **Implements an FSM (Finite State Machine) conversation** — a 4-step guided flow
   that walks new users through setting up their profile. The bot remembers which
   step each user is on, even if they go idle for hours and come back later.

---

## Why It's Built This Way

### Why separate Profile from User?

The `users` table was designed in Phase 4 for **identity and authentication** — it
answers "Is this person registered? Are they banned?" The `profiles` table answers
a completely different question: "What should we know to match them?"

Separating them means:

- **Schema evolution is safer.** Adding new matchmaking fields (interests, preferred
  partner age, location) only touches the profiles table. The identity table stays
  stable.
- **Access control is clearer.** A moderation query only needs the users table. A
  matchmaking query only needs profiles. No monolithic "god table" that everything
  depends on.
- **One-to-one relationship is explicit.** Every profile has exactly one user. If a
  user is deleted, the profile cascades away (ON DELETE CASCADE).

### Why Inline Keyboards instead of free-text input?

Free-text input for structured fields is a UX anti-pattern in bots:

- Users misspell things ("femal", "Engish").
- You need validation logic for every possible typo.
- The conversation becomes frustrating instead of smooth.

Inline Keyboards solve all of this: the user taps a button, and we receive a clean
`callback_data` string like `onb_gender_male`. No parsing, no validation, no typos.
The bot feels polished and fast.

### Why a Finite State Machine?

The onboarding flow is a **multi-step conversation**: gender → age → language → bio.
Without state tracking, if a user sends a message, the bot has no idea whether it's
a gender answer, an age answer, or a bio. The FSM solves this by assigning each user
a **current state** that determines which handler processes their next input.

Key properties of our FSM:

- **Stateless server.** The FSM state is stored by aiogram (in memory by default,
  Redis in production). The bot process can restart and users resume where they left
  off (with Redis storage).
- **Guarded transitions.** Each handler only fires when the user is in the correct
  state. Sending random text while in `waiting_for_gender` is safely ignored because
  only the callback query handler is listening.
- **Clear progression.** The user always knows what the bot is asking for, because
  each step explicitly says "Now select your age range" before showing the buttons.

### Why age ranges instead of exact ages?

Storing an exact age creates a privacy risk — combined with gender and language, it
could narrow down someone's identity. Age *ranges* (18–24, 25–34, etc.) provide
enough granularity for matching without being personally identifiable. We store the
midpoint (21, 30, 40, 50) as the representative value.

### Why is bio optional?

Not everyone wants to share a description of themselves. Forcing it would cause
dropoff during onboarding. The "Skip" button respects user autonomy while still
allowing those who want a richer profile to write something.

### Why does /start also handle re-onboarding?

A user might have hit `/start` months ago, quit before finishing the profile, and
come back. Rather than leaving them in limbo, `/start` checks if they have a
complete profile. If not, it seamlessly restarts the onboarding flow. This makes the
bot self-healing — there's no broken state a user can get stuck in.

---

## How It's Implemented

### The Onboarding Flow

```
User sends /start
       │
       ▼
 Is this a new user?  ────── Yes ──▶  INSERT into users table
       │                                    │
       No                                   │
       │                                    ▼
 Has complete profile? ──── Yes ──▶  "Welcome back! Use /search"
       │                                  (done)
       No
       │
       ▼
 Set FSM state → waiting_for_gender
 Show gender keyboard: [👨 Male] [👩 Female] [🧑 Other]
       │
       ▼  (user taps button → callback_data = "onb_gender_male")
       │
 Store gender in FSM data
 Set FSM state → waiting_for_age
 Show age keyboard: [18-24] [25-34] [35-44] [45+]
       │
       ▼  (user taps button → callback_data = "onb_age_21")
       │
 Store age in FSM data
 Set FSM state → waiting_for_language
 Show language keyboard: [🇬🇧 English] [🇮🇳 Hindi] [🇪🇸 Spanish] ...
       │
       ▼  (user taps button → callback_data = "onb_lang_en")
       │
 Store language in FSM data
 Set FSM state → waiting_for_bio
 Show: "Write a short bio" + [⏭️ Skip] button
       │
       ├── User types bio text ──▶ validate length ≤ 500
       └── User taps Skip ──────▶ bio = None
       │
       ▼
 _finish_onboarding()
       │
       ├── Call ProfileService.create_profile()
       │      ├── Open DB session
       │      ├── Find User by telegram_id
       │      ├── INSERT into profiles (or UPDATE if re-onboarding)
       │      ├── Commit
       │      └── Return profile
       │
       ├── Clear FSM state (user is now IDLE)
       │
       └── Send summary message:
            "🎉 Profile complete!
             👤 Gender: Male
             📅 Age: 21
             🌐 Language: 🇬🇧 English
             📝 Bio: Not set
             Use /search to find a chat partner."
```

### Layered Architecture

```
┌─────────────────────────────────────────────────┐
│                  Telegram User                  │
└─────────────────────┬───────────────────────────┘
                      │ taps buttons / types text
                      ▼
┌─────────────────────────────────────────────────┐
│          bot/handlers/onboarding.py              │
│  (FSM handlers: gender → age → language → bio)  │
│  Manages FSM state transitions + UI responses   │
└─────────────────────┬───────────────────────────┘
                      │ calls
                      ▼
┌─────────────────────────────────────────────────┐
│       core/users/profile_service.py              │
│  (Business logic: create_profile, get_profile)  │
│  Owns DB session lifecycle: open → commit/close │
└─────────────────────┬───────────────────────────┘
                      │ calls
                      ▼
┌─────────────────────────────────────────────────┐
│     core/users/profile_repository.py             │
│  (DB queries: get_by_user_id, create, update)   │
│  The ONLY layer that touches SQLAlchemy queries │
└─────────────────────┬───────────────────────────┘
                      │ reads/writes
                      ▼
┌─────────────────────────────────────────────────┐
│      PostgreSQL: profiles table                  │
│  id | user_id | gender | age | language | bio   │
│     | is_complete | created_at | updated_at     │
└─────────────────────────────────────────────────┘
```

### File Responsibilities

| File | Layer | Responsibility |
|------|-------|----------------|
| `app/core/users/profile_models.py` | Data | Defines the `profiles` table schema |
| `app/core/users/profile_repository.py` | Data Access | All DB queries for profiles — `get_by_user_id`, `create`, `update` |
| `app/core/users/profile_service.py` | Business Logic | Session lifecycle, profile creation/update, `has_complete_profile` check |
| `app/bot/states.py` | FSM | Defines the 4 onboarding states (gender → age → language → bio) |
| `app/bot/keyboards.py` | UI | Inline keyboard builders for each onboarding step |
| `app/bot/handlers/onboarding.py` | Presentation | FSM callback handlers — one per step, plus finalization |
| `app/bot/handlers/start.py` | Entry Point | Updated to kick off onboarding for new/incomplete users |
| `app/bot/router.py` | Wiring | Now includes both `start` and `onboarding` routers |
| `alembic/versions/..._create_profiles_table.py` | Migration | `CREATE TABLE profiles` with FK to users |

### Database Table: `profiles`

| Column | Type | Nullable | Purpose |
|--------|------|----------|---------|
| `id` | UUID (PK) | No | Random internal identifier |
| `user_id` | UUID (FK → users.id, Unique, Indexed) | No | One-to-one link to the user |
| `gender` | VARCHAR(20) | No | male, female, or other |
| `age` | Integer | No | Midpoint of selected age range (21, 30, 40, 50) |
| `language` | VARCHAR(10) | No | ISO 639-1 code (en, hi, es, fr, de, ru) |
| `bio` | Text | Yes | Optional short description |
| `is_complete` | Boolean | No | True once onboarding finishes successfully |
| `created_at` | Timestamp with TZ | No | When the profile was first created |
| `updated_at` | Timestamp with TZ | No | Auto-updates on any row change |

### callback_data Naming Convention

All onboarding callbacks follow the pattern `onb_<step>_<value>`:

| Button | callback_data | Parsed value |
|--------|--------------|--------------|
| 👨 Male | `onb_gender_male` | `"male"` |
| 👩 Female | `onb_gender_female` | `"female"` |
| 🧑 Other | `onb_gender_other` | `"other"` |
| 18-24 | `onb_age_21` | `21` |
| 25-34 | `onb_age_30` | `30` |
| 35-44 | `onb_age_40` | `40` |
| 45+ | `onb_age_50` | `50` |
| 🇬🇧 English | `onb_lang_en` | `"en"` |
| ⏭️ Skip | `onb_bio_skip` | `None` |

---

## What Comes Next (Phase 6)

With profiles stored in the database, Phase 6 builds:

- **`/edit` command** — allows users to re-enter the onboarding flow to change their
  gender, age, language, or bio at any time.
- **Search Preferences model** — a separate table storing *what kind of partner* the
  user wants (preferred gender, age range, language). This is distinct from the
  user's own profile.
- **`/settings` command** — UI for updating search preferences without changing the
  user's own profile.
