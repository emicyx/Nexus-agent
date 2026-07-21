"""UserMemory ORM 模型 — 用户长期记忆（偏好/事实/经验摘要）

跨会话持久化，按 crew_id 隔离。通过 pgvector 语义检索，在 kickoff 前注入 task context。
与 CrewAI 内置 LongTermMemory（TaskEvaluator 任务质量评估）职责不同，互不干扰。
"""
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.base import Base, TimestampMixin

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # 类型检查环境可能没有 pgvector


class UserMemory(Base, TimestampMixin):
    """一条用户长期记忆，带 embedding 向量供语义检索。"""

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    crew_id: Mapped[int] = mapped_column(
        ForeignKey("crew_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # memory_type: user_preference | user_fact | experience_summary
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    if Vector is not None:
        embedding = mapped_column(
            Vector(settings.EMBEDDING_DIM), nullable=False
        )
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[str | None] = mapped_column(nullable=True)
