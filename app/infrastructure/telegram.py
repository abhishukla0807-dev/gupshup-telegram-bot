"""
Telegram Bot infrastructure — singleton provider, command registration, and lifecycle.

Centralizes all Telegram Bot API setup so that:
  - The Bot instance is created once and shared across the application.
  - Bot commands (the '/' autocomplete menu) are registered in one place.
  - Handler modules can import `get_bot()` without circular dependencies.
  - main.py stays minimal — just calls init/start/shutdown.

Mirrors the pattern used by redis.py (connection pool) and database.py (engine).
"""
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from app.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------
_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None

# ---------------------------------------------------------------------------
# Bot command definitions
# ---------------------------------------------------------------------------
BOT_COMMANDS = [
    BotCommand(command="search", description="🔍 Find an anonymous chat partner"),
    BotCommand(command="stop", description="⏹ Stop searching / cancel queue"),
    BotCommand(command="next", description="⏭ Skip to next partner"),
    BotCommand(command="end", description="🛑 End current chat session"),
    BotCommand(command="profile", description="👤 View and edit your profile"),
    BotCommand(command="settings", description="⚙️ Matching & search preferences"),
    BotCommand(command="block", description="🚫 Block partner and disconnect"),
    BotCommand(command="help", description="📖 User guide & commands index"),
    BotCommand(command="start", description="🚀 Main dashboard / restart bot"),
]


# ---------------------------------------------------------------------------
# Lifecycle functions
# ---------------------------------------------------------------------------
def get_bot() -> Bot:
    """
    Return the global Bot singleton.

    Must be called after `init_bot()`. Raises RuntimeError if the bot
    has not been initialised yet.
    """
    if _bot is None:
        raise RuntimeError(
            "Telegram Bot has not been initialised. Call init_bot() first."
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    """
    Return the global Dispatcher singleton.

    Must be called after `init_bot()`. Raises RuntimeError if the dispatcher
    has not been initialised yet.
    """
    if _dispatcher is None:
        raise RuntimeError(
            "Telegram Dispatcher has not been initialised. Call init_bot() first."
        )
    return _dispatcher


def init_bot(token: str | None = None) -> tuple[Bot, Dispatcher]:
    """
    Create the Bot + Dispatcher singletons.

    Called once at application startup. Returns the pair so `main.py`
    can use them directly without a second `get_*` call.

    Args:
        token: Telegram bot token. Defaults to ``settings.TELEGRAM_BOT_TOKEN``.
    """
    global _bot, _dispatcher

    if _bot is not None:
        logger.debug("init_bot() called again — returning existing instances.")
        return _bot, _dispatcher  # type: ignore[return-value]

    resolved_token = token or settings.TELEGRAM_BOT_TOKEN
    _bot = Bot(token=resolved_token)
    _dispatcher = Dispatcher()

    logger.info("Telegram Bot and Dispatcher initialised.")
    return _bot, _dispatcher


async def setup_bot_commands(bot: Bot | None = None) -> None:
    """
    Register all slash-commands with the Telegram API so users see
    the autocomplete menu when they type '/'.

    Also enables the persistent menu button in the chat UI.
    """
    target = bot or get_bot()
    try:
        await target.set_my_commands(
            commands=BOT_COMMANDS,
            scope=BotCommandScopeDefault(),
        )
        await target.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info(
            "Telegram UI: %d bot commands registered, menu button enabled.",
            len(BOT_COMMANDS),
        )
    except Exception as e:
        logger.warning("Could not register Telegram UI bot commands (non-fatal): %s", e)



async def close_bot() -> None:
    """
    Gracefully close the Bot HTTP session at shutdown.

    Safe to call even if the bot was never initialised.
    """
    global _bot, _dispatcher
    if _bot is not None:
        await _bot.session.close()
        logger.info("Telegram Bot session closed.")
        _bot = None
    _dispatcher = None
