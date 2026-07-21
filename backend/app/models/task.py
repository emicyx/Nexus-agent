"""TaskConfig ORM 模型"""
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent import AgentConfig
    from app.models.crew import CrewConfig
    from app.models.output_schema import OutputSchemaConfig


class TaskConfig(Base, TimestampMixin):
    """Task 配置：属于某 Crew，由某 Agent 执行，可依赖其他 Task。"""
    __tablename__ = "task_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crew_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("crew_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 依赖的其他 task id 列表（JSONB 数组），构造 Crew 时解析为 context
    context_task_ids: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    # 输出格式模板（可选 FK）
    output_schema_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("output_schema_configs.id", ondelete="SET NULL"), nullable=True
    )

    crew: Mapped["CrewConfig"] = relationship(back_populates="tasks")
    output_schema: Mapped["OutputSchemaConfig | None"] = relationship(
        foreign_keys=[output_schema_id], lazy="selectin",
    )
    # agent 关系不强制加载（避免循环），按需查询

    def __repr__(self) -> str:
        return f"<TaskConfig {self.id} {self.name} crew={self.crew_id}>"
