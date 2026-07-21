"""Chat 会话与消息持久化模型（Week 11+）

将对话历史从 Redis（TTL 1h，重启丢失）迁移到 PostgreSQL 持久化，
让用户切换 crew / 重启服务后仍可恢复历史会话继续对话。
"""
from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ChatSession(Base, TimestampMixin):
    """一个对话会话，绑定到某个 Crew。session_uuid 由前端生成（uuid）。"""
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    crew_id: Mapped[int] = mapped_column(
        ForeignKey("crew_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新对话")

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base, TimestampMixin):
    """会话内的单条消息（user 或 assistant）。"""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
