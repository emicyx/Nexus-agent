"""DocumentConfig / DocumentChunk ORM 模型（Week 4 RAG）

- DocumentConfig：文档元信息（名称、来源类型、原文）
- DocumentChunk：分块 + pgvector 向量列，用于语义检索
"""
from typing import TYPE_CHECKING, Any, List

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class DocumentConfig(Base, TimestampMixin):
    """文档配置：上传的原始文档元信息。"""
    __tablename__ = "document_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")  # text | file
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 原文（text 类型直接存）

    # 反向：分块
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<DocumentConfig {self.id} {self.name}>"


class DocumentChunk(Base, TimestampMixin):
    """文档分块 + 向量。

    embedding 维度由 settings.EMBEDDING_DIM 决定（默认 1024）。
    用 cosine 距离操作符 <=> 检索。
    """
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("document_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    # 混合检索：tsvector 生成列（to_tsvector('chinese', content)），GIN 索引
    tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('chinese', content)", persisted=True),
        nullable=True,
    )

    # 反向：所属文档
    document: Mapped["DocumentConfig"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<DocumentChunk {self.id} doc={self.document_id} pos={self.position}>"
