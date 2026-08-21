"""RAG 检索工具（Week 4 + Week 9 混合检索）

对接 pgvector + zhparser 中文全文检索，让 Agent 能自主决定何时调用知识库检索。
- _run 是同步方法，CrewAI akickoff() 在主事件循环中调用，不能用 asyncio.run
- 同步 DB Session（db.session.get_sync_session）+ 同步 embedding（llm.embedding.embed_texts_sync）
- 混合检索 SQL 与 API 路径共用 services/hybrid_search.py
"""
import logging
from typing import Any, Optional, Union

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text as sa_text

from app.db.session import get_sync_session, vec_to_sql_literal
from app.llm.embedding import embed_texts_sync
from app.services.hybrid_search import (
    HYBRID_SQL,
    LEXEME_SQL,
    build_tsquery,
    hybrid_params,
    map_rows,
)

logger = logging.getLogger("rag_tool")


def _search_sync(
    query: str,
    top_k: int,
    document_id: int | None = None,
) -> list[dict]:
    """同步执行向量 + 关键词 RRF 混合检索。"""
    query_vec = embed_texts_sync([query])[0]
    vec_literal = vec_to_sql_literal(query_vec)
    with get_sync_session() as session:
        # 关键词 tsquery：zhparser 切词 + OR（过滤停用词），修复全 AND 零命中
        lexemes = session.execute(LEXEME_SQL, {"q": query}).scalars().all()
        tsq = build_tsquery(list(lexemes))
        rows = session.execute(
            HYBRID_SQL, hybrid_params(vec_literal, tsq, document_id, top_k)
        ).all()
        return map_rows(rows)


class RagSearchInput(BaseModel):
    """RAG 检索工具输入。"""
    query: str = Field(
        ...,
        description=(
            "要在知识库中检索的问题或关键词，不能为空。"
            "适用场景：用户提问涉及已上传的私有文档/资料时，"
            "应优先使用本工具而非搜索引擎。"
        ),
    )
    top_k: Optional[Union[int, str]] = Field(
        5,
        description="返回的最相关分块数量，默认5，推荐3-8。",
    )
    document_id: Optional[Union[int, str]] = Field(
        None,
        description=(
            "可选：限定在指定文档 ID 内检索，不填则跨全部已上传文档检索。"
            "适合用户明确指明在某份文档里查的场景。"
        ),
    )

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query 不能为空")
        return v.strip()

    @field_validator("top_k")
    @classmethod
    def _validate_top_k(cls, v: Union[int, str]) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"top_k 必须是整数，收到: {v}")
        if v <= 0 or v > 20:
            raise ValueError("top_k 必须在 1-20 之间")
        return v

    @field_validator("document_id")
    @classmethod
    def _validate_document_id(cls, v: Union[int, str, None]) -> int | None:
        if v in (None, "", 0):
            return None
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"document_id 必须是正整数，收到: {v}")
        if v <= 0:
            raise ValueError("document_id 必须为正整数")
        return v


class RagSearchTool(BaseTool):
    """
    知识库语义检索工具（Agentic RAG）。

    在已上传的私有文档中做语义检索，返回最相关的分块。
    当用户提问涉及知识库内容（如公司规章、产品手册、内部资料）时，
    应优先调用本工具而非联网搜索引擎。
    """
    name: str = "rag_search"
    description: str = (
        "在已上传的私有知识库中做语义检索，返回最相关的文本片段。"
        "触发时机：用户提问涉及知识库已收录的资料（如公司规章、产品手册、内部文档、上传的文件内容）时使用。"
        "适用边界：当问题需要私有/内部信息时使用本工具；当问题需要公开网络信息（如最新新闻、通用知识）时改用 search_web。"
    )
    args_schema: type[BaseModel] = RagSearchInput

    # Week 7: 参数化 — config_json 中的 top_k 注入为默认值
    top_k_default: int = 5

    def _run(
        self,
        query: str,
        top_k: Union[int, str] = 5,
        document_id: Union[int, str, None] = None,
        **kwargs: Any,
    ) -> str:
        """同步入口：用同步 DB + 同步 embedding，避开 asyncio。"""
        try:
            top_k_int = int(top_k)
        except (TypeError, ValueError):
            top_k_int = self.top_k_default
        if top_k_int <= 0 or top_k_int > 20:
            top_k_int = self.top_k_default

        doc_id: int | None = None
        if document_id not in (None, "", 0):
            try:
                doc_id = int(document_id)
            except (TypeError, ValueError):
                doc_id = None
            if doc_id is not None and doc_id <= 0:
                doc_id = None

        try:
            results = _search_sync(query.strip(), top_k_int, document_id=doc_id)
        except Exception as e:
            logger.exception("rag_search_failed")
            return f"知识库检索出错: {e}"

        if not results:
            return (
                "知识库为空或未找到相关内容。"
                "提示：可能是知识库尚未上传文档，或问题与知识库内容无关。"
            )

        lines = [f"在知识库中找到 {len(results)} 条相关结果：", ""]
        for idx, r in enumerate(results, 1):
            score = r.get("score", 0.0)
            doc_name = r.get("document_name", "?")
            content = r.get("content", "")
            lines.append(
                f"结果{idx}: [{doc_name}] (相似度={score:.3f})\n  内容: {content}\n"
            )
        return "\n".join(lines)
