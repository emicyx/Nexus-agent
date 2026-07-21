"""ToolConfig ORM 模型"""
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.association import AgentTool
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent import AgentConfig


class ToolConfig(Base, TimestampMixin):
    """Tool 配置：tool_key 映射到注册表，config_json 存参数。"""
    __tablename__ = "tool_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    tool_key: Mapped[str] = mapped_column(String(64), nullable=False)  # 注册表键
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    # 反向：哪些 Agent 挂载了此工具
    agents: Mapped[List["AgentConfig"]] = relationship(
        secondary=AgentTool,
        back_populates="tools",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ToolConfig {self.id} {self.name}={self.tool_key}>"
