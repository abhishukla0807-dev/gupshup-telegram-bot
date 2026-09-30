"""
Application entry point.

Starts the aiogram bot with long-polling (development mode).
Initializes Redis connection, background MatchmakingWorker,
ReconciliationJanitor, and state middleware.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path so running directly as `python app/main.py` works
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.bot.middlewares.state_middleware import UserStateMiddleware
from app.bot.router import main_router
from app.config.settings import settings
from app.core.matching.worker import MatchmakingWorker, ReconciliationJanitor
from app.infrastructure.redis import close_redis, get_redis_client, init_redis
from app.infrastructure.telegram import close_bot, init_bot, setup_bot_commands

# Configure structured-friendly logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Initialize the bot, workers, and start polling."""
    logger.info("Starting GupShup matchmaker in %s mode...", settings.ENVIRONMENT)

    # 1. Initialize Redis connection pool & verify reachability
    await init_redis()
    client = get_redis_client()

    # Prevent duplicate polling instances (TelegramConflictError)
    lock_key = f"bot:instance:lock:{settings.TELEGRAM_BOT_TOKEN[:10]}"
    pid = os.getpid()
    acquired = await client.set(lock_key, str(pid), nx=True, ex=30)
    if not acquired:
        existing_pid = await client.get(lock_key)
        logger.error(
            "CRITICAL: Another bot instance (PID=%s) is already running and polling Telegram! "
            "To prevent TelegramConflictError, this instance will not start. "
            "Terminate the existing instance if you wish to restart.",
            existing_pid,
        )
        await close_redis()
        return

    # Background heartbeat to renew instance lock while running
    async def _heartbeat_lock():
        try:
            while True:
                await asyncio.sleep(10)
                await client.expire(lock_key, 30)
        except asyncio.CancelledError:
            pass

    heartbeat_task = asyncio.create_task(_heartbeat_lock())

    # 2. Initialize Telegram Bot & Dispatcher via infrastructure layer
    bot, dp = init_bot()

    # 3. Register runtime state middleware
    dp.message.middleware(UserStateMiddleware())
    dp.callback_query.middleware(UserStateMiddleware())

    # 4. Register aggregated bot routers
    dp.include_router(main_router)

    # 5. Set up Telegram UI autocomplete commands & chat menu button
    await setup_bot_commands(bot)

    # 6. Initialize background daemons
    matchmaking_worker = MatchmakingWorker(bot=bot, tick_interval_seconds=1.0)
    reconciliation_janitor = ReconciliationJanitor(interval_seconds=60.0)

    # 7. Start background daemons
    await matchmaking_worker.start()
    await reconciliation_janitor.start()

    # Drop any pending updates that arrived while the bot was offline
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("GupShup bot and matchmaking engine are now running (PID=%s)...", pid)
    try:
        await dp.start_polling(bot)
    finally:
        # Cancel lock heartbeat & release instance lock
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await client.delete(lock_key)

        # Graceful teardown in reverse order
        await matchmaking_worker.stop()
        await reconciliation_janitor.stop()
        await close_bot()
        await close_redis()
        logger.info("GupShup bot and workers shut down gracefully.")


if __name__ == "__main__":
    asyncio.run(main())
