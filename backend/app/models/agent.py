"""AgentConfig ORM 模型"""
from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.association import AgentSkill, AgentTool
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.skill import SkillConfig
    from app.models.tool import ToolConfig


class AgentConfig(Base, TimestampMixin):
    """Agent 配置：role/goal/backstory + LLM 参数 + 挂载的工具。"""
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    backstory: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None=用默认
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_iter: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    memory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # M2M → tools（通过 AgentTool 关联表）
    tools: Mapped[List["ToolConfig"]] = relationship(
        secondary=AgentTool,
        back_populates="agents",
        lazy="selectin",
    )

    # M2M → skills（通过 AgentSkill 关联表）
    skills: Mapped[List["SkillConfig"]] = relationship(
        secondary=AgentSkill,
        back_populates="agents",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<AgentConfig {self.id} {self.name}={self.role}>"
