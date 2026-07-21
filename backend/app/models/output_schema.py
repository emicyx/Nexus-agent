"""OutputSchemaConfig ORM 模型 — 可复用的输出格式模板，为 Task 提供 Pydantic 输出契约。"""
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OutputSchemaConfig(Base, TimestampMixin):
    """输出格式模板：定义 JSON schema 字段，运行时动态生成 Pydantic 模型传入 Task(output_pydantic=...)。

    schema_fields JSONB 格式示例：
    [
      {"name": "title", "type": "str", "required": true, "description": "研究主题"},
      {"name": "key_facts", "type": "list[str]", "required": true, "description": "关键事实"},
    ]
    """

    __tablename__ = "output_schema_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_fields: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)

    def __repr__(self) -> str:
        return f"<OutputSchemaConfig {self.id} {self.name}>"
