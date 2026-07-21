"""CrewConfig ORM 模型"""
from typing import TYPE_CHECKING, List

from sqlalchemy import ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.association import CrewAgent
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent import AgentConfig
    from app.models.task import TaskConfig


class CrewConfig(Base, TimestampMixin):
    """Crew 配置：含 agents（有序）+ tasks（有序）。"""
    __tablename__ = "crew_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    process_type: Mapped[str] = mapped_column(String(32), default="sequential", nullable=False)

    # Week 6: hierarchical 模式下的主 Agent（manager_agent）
    manager_agent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_configs.id", ondelete="SET NULL"), nullable=True
    )
    manager_agent: Mapped["AgentConfig | None"] = relationship(
        foreign_keys=[manager_agent_id], lazy="selectin"
    )

    # M2M → agents（通过 CrewAgent，按 position 排序）
    agents: Mapped[List["AgentConfig"]] = relationship(
        secondary=CrewAgent,
        lazy="selectin",
        order_by=CrewAgent.c.position,
    )

    # 1→N tasks
    tasks: Mapped[List["TaskConfig"]] = relationship(
        back_populates="crew",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TaskConfig.position",
    )

    def __repr__(self) -> str:
        return f"<CrewConfig {self.id} {self.name}>"
