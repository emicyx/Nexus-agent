"""知识库写入工具：将 markdown 文本切块+向量化后写入 pgvector。

同步实现（参考 rag_search_tool.py 同步模式）：
- CrewAI akickoff() 在主事件循环调用 _run，不能用 asyncio.run
- 用同步 SQLAlchemy（psycopg2）+ 同步 requests 调 embedding API
- 切块逻辑复用 document_service._split_chunks

表结构：
- document_configs(id, name, source_type, content_text, created_at, updated_at)
- document_chunks(id, document_id, content, embedding Vector(dim), position,
                  metadata_json, tsv [generated column to_tsvector('chinese', content)])
"""
import json
import logging
import os
from typing import Any

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.services.document_service import _split_chunks

logger = logging.getLogger("kb_ingest")

# 同步 engine（psycopg2），延迟初始化避免启动时连不上 DB
_sync_engine = None
_SyncSessionLocal = None


def _get_sync_session() -> Session:
    """延迟初始化同步 DB engine，返回 session。"""
    global _sync_engine, _SyncSessionLocal
    if _SyncSessionLocal is None:
        dsn = settings.POSTGRES_DSN
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        _sync_engine = create_engine(dsn, pool_pre_ping=True, future=True)
        _SyncSessionLocal = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return _SyncSessionLocal()


def _embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """同步批量调用 DashScope embedding API。

    DashScope /v1/embeddings 接口单次最多 25 条输入，超出时自动分批。
    """
    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 QWEN_API_KEY")
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    batch_size = 25
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        payload = {
            "model": settings.EMBEDDING_MODEL,
            "input": batch,
            "dimensions": settings.EMBEDDING_DIM,
            "encoding_format": "float",
        }
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        # DashScope 返回按 input 顺序排列
        all_embeddings.extend([d["embedding"] for d in data])
    return all_embeddings


def _vec_to_sql_literal(vec: list[float]) -> str:
    """把向量列表转成 pgvector 接受的字符串字面量。"""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# SQL：插入文档
_INSERT_DOC_SQL = sa_text(
    """
    INSERT INTO document_configs (name, source_type, content_text, created_at, updated_at)
    VALUES (:name, :source_type, :content, NOW(), NOW())
    RETURNING id
    """
)

# SQL：插入分块（embedding 用 CAST 转为 vector 类型）
_INSERT_CHUNK_SQL = sa_text(
    """
    INSERT INTO document_chunks
        (document_id, content, embedding, position, metadata_json, created_at, updated_at)
    VALUES
        (:doc_id, :content, CAST(:emb AS vector), :position, CAST(:meta AS jsonb), NOW(), NOW())
    RETURNING id
    """
)


class KbIngestInput(BaseModel):
    """知识库入库工具输入。"""

    name: str = Field(
        ...,
        description=(
            "文档名称（用于知识库检索时展示来源）。"
            "建议用网页标题或 URL 简化形式。不能为空。"
        ),
    )
    content: str = Field(
        ...,
        description=(
            "要入库的 markdown 文本内容。工具会自动切块、向量化、写入知识库。"
            "不能为空。"
        ),
    )
    source_type: str = Field(
        "web",
        description="来源类型标记，默认 'web'（网页抓取）。可选 'text'、'file'、'web'。",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name 不能为空")
        return v.strip()

    @field_validator("content")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content 不能为空")
        return v

    @field_validator("source_type")
    @classmethod
    def _validate_source_type(cls, v: str) -> str:
        v = (v or "web").strip() or "web"
        if v not in ("text", "file", "web"):
            raise ValueError("source_type 必须是 text/file/web 之一")
        return v


class KbIngestTool(BaseTool):
    """
    知识库入库工具。

    将 markdown 文本写入知识库（切块 + 向量化 + pgvector 存储），
    入库后即可被 rag_search 工具检索到。

    触发时机：编排主管审批通过 markdown 内容后，由知识库写入员调用。
    """

    name: str = "kb_ingest"
    description: str = (
        "将 markdown 文本写入知识库（自动切块、向量化、存入 pgvector）。"
        "触发时机：当编排主管审批通过 markdown 内容、"
        "确认达到入库标准后，由知识库写入员调用执行入库。"
        "适用边界：调用前必须经编排主管审批；入库后不可撤销，"
        "但可通过文档管理 API 删除。"
    )
    args_schema: type[BaseModel] = KbIngestInput

    def _run(
        self,
        name: str,
        content: str,
        source_type: str = "web",
        **kwargs: Any,
    ) -> str:
        """同步入口：切块→批量嵌入→同步写库。"""
        # 参数防御（即便 pydantic 已校验，CrewAI 调用时可能传非 str）
        if not name or not str(name).strip():
            return "错误：name 不能为空"
        if not content or not str(content).strip():
            return "错误：content 不能为空"
        name = str(name).strip()
        content = str(content)
        source_type = str(source_type or "web").strip() or "web"
        if source_type not in ("text", "file", "web"):
            source_type = "web"

        # 切块
        try:
            chunks = _split_chunks(content)
        except Exception as e:
            logger.exception("split_chunks_failed")
            return f"切块失败：{e}"

        if not chunks:
            return "错误：内容为空，无法切块入库"

        # 批量嵌入
        try:
            embeddings = _embed_texts_sync(chunks)
        except Exception as e:
            logger.exception("embed_failed")
            return f"向量化失败：{e}"

        if len(embeddings) != len(chunks):
            return (
                f"嵌入数量不匹配：{len(embeddings)} != {len(chunks)}，"
                "请重试或检查 DashScope 接口"
            )

        # 同步写库
        try:
            with _get_sync_session() as session:
                # 插入文档
                doc_id = session.execute(
                    _INSERT_DOC_SQL,
                    {
                        "name": name,
                        "source_type": source_type,
                        "content": content,
                    },
                ).scalar()
                if doc_id is None:
                    session.rollback()
                    return "错误：插入文档失败（未返回 id）"

                # 逐块插入分块
                for idx, (chunk_text, emb) in enumerate(
                    zip(chunks, embeddings)
                ):
                    emb_literal = _vec_to_sql_literal(emb)
                    meta = json.dumps(
                        {"source": name, "position": idx},
                        ensure_ascii=False,
                    )
                    session.execute(
                        _INSERT_CHUNK_SQL,
                        {
                            "doc_id": doc_id,
                            "content": chunk_text,
                            "emb": emb_literal,
                            "position": idx,
                            "meta": meta,
                        },
                    )
                session.commit()
                logger.info(
                    "kb_ingest_ok: doc_id=%s chunks=%d dim=%d",
                    doc_id, len(chunks), len(embeddings[0]),
                )
                return (
                    f"已入库：doc_id={doc_id}，"
                    f"chunks={len(chunks)}，"
                    f"source_type={source_type}，"
                    f"dim={len(embeddings[0])}。"
                    f"现在可以通过 rag_search 工具检索该文档内容。"
                )
        except Exception as e:
            logger.exception("kb_ingest_db_failed")
            return f"入库失败（DB 错误）：{e}"
