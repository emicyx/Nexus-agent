"""SkillConfig ORM 模型

Skills = 可复用的指令模板 + 关联工具包。
Agent 挂载 skill 后，prompt_template 注入 backstory，获得该领域能力。
"""
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.association import AgentSkill
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent import AgentConfig


class SkillConfig(Base, TimestampMixin):
    """Skill 配置：prompt_template 指令模板，注入 agent backstory。"""
    __tablename__ = "skill_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    skill_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    # 反向：哪些 Agent 挂载了此 skill
    agents: Mapped[List["AgentConfig"]] = relationship(
        secondary=AgentSkill,
        back_populates="skills",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<SkillConfig {self.id} {self.name}>"
