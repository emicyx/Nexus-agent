"""知识库写入工具：将 markdown 文本切块+向量化后写入 pgvector。

同步实现（参考 rag_search_tool.py 同步模式）：
- CrewAI akickoff() 在主事件循环调用 _run，不能用 asyncio.run
- 同步 DB Session（db.session.get_sync_session）+ 同步 embedding（llm.embedding.embed_texts_sync）
- 切块逻辑：semantic_chunker.semantic_chunk（句子级 Embedding 相似度语义分块）

表结构：
- document_configs(id, name, source_type, content_text, created_at, updated_at)
- document_chunks(id, document_id, content, embedding Vector(dim), position,
                  metadata_json, tsv [generated column to_tsvector('chinese', content)])
"""
import json
import logging
import time
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text as sa_text

from app.db.session import get_sync_session, vec_to_sql_literal
from app.llm.embedding import embed_texts_sync
from app.services.semantic_chunker import semantic_chunk

logger = logging.getLogger("kb_ingest")


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

        # 语义切块（句子级 + Embedding 相似度边界，内部同步嵌入）
        t0 = time.perf_counter()
        try:
            chunks = semantic_chunk(content)
        except Exception as e:
            logger.exception("semantic_chunk_failed")
            return f"语义切块失败：{e}"

        if not chunks:
            return "错误：内容为空，无法切块入库"
        logger.info(
            "kb_ingest split_chunks: chars=%d, chunks=%d, took=%.3fs",
            len(content), len(chunks), time.perf_counter() - t0,
        )

        # 批量嵌入
        t1 = time.perf_counter()
        try:
            embeddings = embed_texts_sync(chunks)
        except Exception as e:
            logger.exception("embed_failed")
            return f"向量化失败：{e}"
        logger.info(
            "kb_ingest embed: chunks=%d, dim=%d, batch_count=%d, took=%.3fs",
            len(chunks), len(embeddings[0]) if embeddings else 0,
            -(-len(chunks) // 6), time.perf_counter() - t1,
        )

        if len(embeddings) != len(chunks):
            return (
                f"嵌入数量不匹配：{len(embeddings)} != {len(chunks)}，"
                "请重试或检查 DashScope 接口"
            )

        # 同步写库
        t2 = time.perf_counter()
        try:
            with get_sync_session() as session:
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
                    emb_literal = vec_to_sql_literal(emb)
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
                    "kb_ingest_ok: doc_id=%s chunks=%d dim=%d db_took=%.3fs total_took=%.3fs",
                    doc_id, len(chunks), len(embeddings[0]),
                    time.perf_counter() - t2,
                    time.perf_counter() - t0,
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
