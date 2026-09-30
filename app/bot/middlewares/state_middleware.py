"""
User State Middleware — injects the current runtime matchmaking state into every bot event.

Handlers can declare `user_match_state: UserStateData` and/or
`match_state_mgr: UserStateManager` as arguments, and Aiogram will
inject them automatically from the event context.

Also enforces instant ban blocking if a user is flagged in Redis.
"""
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.core.sessions.state_machine import UserStateManager
from app.infrastructure.redis import get_redis_client


class UserStateMiddleware(BaseMiddleware):
    """
    Aiogram middleware that resolves and injects user runtime matchmaking state
    from Redis for every incoming message or callback query.
    """

    def __init__(self, state_manager: UserStateManager | None = None) -> None:
        super().__init__()
        self.state_manager = state_manager or UserStateManager()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        event_user: User | None = data.get("event_from_user")
        if event_user is not None:
            # Fast Redis ban enforcement
            try:
                client = get_redis_client()
                is_banned = await client.get(f"user:banned:{event_user.id}")
                if is_banned:
                    if isinstance(event, Message):
                        await event.answer(
                            "🚫 <b>Account Suspended:</b> You have been banned from using GupShup "
                            "due to violations of our community guidelines.",
                            parse_mode="HTML",
                        )
                    elif isinstance(event, CallbackQuery):
                        await event.answer("🚫 Account suspended.", show_alert=True)
                    return None
            except Exception:
                pass

            data["match_state_mgr"] = self.state_manager
            data["user_match_state"] = await self.state_manager.get_state(event_user.id)

        return await handler(event, data)
