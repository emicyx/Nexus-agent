"""会话滚动摘要模型（STM Layer 1 滑动窗口 + 滚动摘要）"""
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChatSessionSummary(Base, TimestampMixin):
    """每个会话一条滚动摘要（增量合并，last_message_id 作水位线保证幂等）。

    last_message_id: 本摘要已覆盖的最大 chat_messages.id（单调递增，绝不回退）。
    """

    __tablename__ = "chat_session_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
