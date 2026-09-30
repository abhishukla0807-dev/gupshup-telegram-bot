"""
Session repository — database operations for ChatSession and UserActiveSession.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions.models import ChatSession, UserActiveSession


class SessionRepository:
    """Encapsulates all database operations for durable chat sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        user1_id: uuid.UUID,
        user2_id: uuid.UUID,
    ) -> ChatSession:
        """
        Atomically create a new active chat session for two users.

        Guaranteed invariant:
        If user1 or user2 already has an active session, PostgreSQL will
        raise IntegrityError on the user_active_sessions primary key.
        """
        chat_session = ChatSession(
            user1_id=user1_id,
            user2_id=user2_id,
            status="active",
        )
        self.session.add(chat_session)
        await self.session.flush()

        # Enforce exactly one active session per user
        active1 = UserActiveSession(user_id=user1_id, session_id=chat_session.id)
        active2 = UserActiveSession(user_id=user2_id, session_id=chat_session.id)
        self.session.add_all([active1, active2])
        await self.session.flush()

        return chat_session

    async def end_session(
        self,
        session_id: uuid.UUID,
        ended_reason: str = "user_left",
    ) -> bool:
        """
        Atomically terminate a chat session.

        Idempotent: returns True if this call ended the session,
        or False if it was already ended.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.status == "active")
            .values(
                status="ended",
                ended_reason=ended_reason,
                ended_at=now,
            )
        )
        result = await self.session.execute(stmt)

        # Remove both participants from user_active_sessions
        del_stmt = delete(UserActiveSession).where(
            UserActiveSession.session_id == session_id
        )
        await self.session.execute(del_stmt)
        await self.session.flush()

        return result.rowcount > 0

    async def get_by_id(self, session_id: uuid.UUID) -> Optional[ChatSession]:
        """Fetch session by its UUID."""
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_session_by_user_id(
        self,
        user_id: uuid.UUID,
    ) -> Optional[ChatSession]:
        """Fetch the active session for a user, or None if idle."""
        stmt = (
            select(ChatSession)
            .join(UserActiveSession, UserActiveSession.session_id == ChatSession.id)
            .where(
                UserActiveSession.user_id == user_id,
                ChatSession.status == "active",
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
