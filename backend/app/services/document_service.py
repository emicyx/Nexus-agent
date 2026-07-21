"""文档 RAG 服务（Week 4 + Week 9 混合检索）

- ingest_document: 切块 + 嵌入 + 写库
- search_documents: 向量 + 关键词 RRF 融合检索
- list_documents / delete_document
"""
import logging
from typing import Any

from sqlalchemy import delete, func, literal, select
from sqlalchemy.sql import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embedding import embed_query, embed_texts
from app.models import DocumentChunk, DocumentConfig

logger = logging.getLogger("document_service")

# 分块参数
_CHUNK_MAX_CHARS = 500
_PARA_SEP = "\n\n"

# RRF 融合参数
_RRF_K = 60
# 每路预取上限（控制扫描成本）
_RRF_POOL = 200


def _split_chunks(text: str) -> list[str]:
    """按段落切块，单段超过上限时硬切。"""
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split(_PARA_SEP) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= _CHUNK_MAX_CHARS:
            chunks.append(para)
        else:
            # 硬切
            for i in range(0, len(para), _CHUNK_MAX_CHARS):
                chunks.append(para[i : i + _CHUNK_MAX_CHARS])
    return chunks


async def ingest_document(
    session: AsyncSession,
    name: str,
    content: str,
    source_type: str = "text",
) -> DocumentConfig:
    """切块 + 嵌入 + 写入文档与分块。"""
    chunks_text = _split_chunks(content)
    if not chunks_text:
        raise ValueError("文档内容为空，无法切块")

    # 批量嵌入
    embeddings = await embed_texts(chunks_text)
    if len(embeddings) != len(chunks_text):
        raise ValueError(f"嵌入数量不匹配：{len(embeddings)} != {len(chunks_text)}")

    doc = DocumentConfig(
        name=name,
        source_type=source_type,
        content_text=content,
    )
    session.add(doc)
    await session.flush()  # 拿到 doc.id

    chunk_objs = [
        DocumentChunk(
            document_id=doc.id,
            content=txt,
            embedding=emb,
            position=idx,
            metadata_json={"source": name, "position": idx},
        )
        for idx, (txt, emb) in enumerate(zip(chunks_text, embeddings))
    ]
    session.add_all(chunk_objs)
    await session.commit()
    await session.refresh(doc)
    logger.info(
        "ingest_document: doc=%s chunks=%d dim=%d",
        doc.id, len(chunk_objs), len(embeddings[0]),
    )
    return doc


async def search_documents(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
    document_id: int | None = None,
) -> list[dict[str, Any]]:
    """混合检索：向量 + 关键词 RRF 融合。

    向量路用 pgvector cosine_distance，关键词路用 zhparser 中文分词 ts_rank。
    两路各取前 _RRF_POOL 条，用 RRF(k=60) 融合后取 top_k。

    可选 document_id 限定检索范围。

    返回 [{content, document_name, score(=rrf_score), position}]
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    query_vec = await embed_query(q)
    tsq = func.plainto_tsquery("chinese", q)

    # 向量路 CTE
    vec_dist = DocumentChunk.embedding.cosine_distance(query_vec)
    vec_sub = (
        select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.content.label("content"),
            DocumentChunk.position.label("position"),
            DocumentConfig.name.label("document_name"),
            func.row_number().over(order_by=vec_dist).label("rn"),
        )
        .join(DocumentConfig, DocumentChunk.document_id == DocumentConfig.id)
        .order_by(vec_dist)
        .limit(_RRF_POOL)
    )
    if document_id is not None:
        vec_sub = vec_sub.where(DocumentChunk.document_id == document_id)
    vec_cte = vec_sub.cte("vec")

    # 关键词路 CTE
    kw_rank = func.ts_rank(DocumentChunk.tsv, tsq)
    kw_sub = (
        select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.content.label("content"),
            DocumentChunk.position.label("position"),
            DocumentConfig.name.label("document_name"),
            func.row_number().over(order_by=kw_rank.desc()).label("rn"),
        )
        .join(DocumentConfig, DocumentChunk.document_id == DocumentConfig.id)
        .where(DocumentChunk.tsv.op("@@")(tsq))
        .order_by(kw_rank.desc())
        .limit(_RRF_POOL)
    )
    if document_id is not None:
        kw_sub = kw_sub.where(DocumentChunk.document_id == document_id)
    kw_cte = kw_sub.cte("kw")

    # FULL OUTER JOIN 后 RRF 融合
    # coalesce(content/position/document_name) 因为两边都有
    rrf_expr = (
        func.coalesce(literal(1.0) / (_RRF_K + vec_cte.c.rn), literal(0.0))
        + func.coalesce(literal(1.0) / (_RRF_K + kw_cte.c.rn), literal(0.0))
    ).label("rrf_score")

    fused = (
        select(
            func.coalesce(vec_cte.c.chunk_id, kw_cte.c.chunk_id).label("chunk_id"),
            func.coalesce(vec_cte.c.content, kw_cte.c.content).label("content"),
            func.coalesce(vec_cte.c.position, kw_cte.c.position).label("position"),
            func.coalesce(vec_cte.c.document_name, kw_cte.c.document_name).label("document_name"),
            rrf_expr,
        )
        .select_from(
            vec_cte.outerjoin(
                kw_cte,
                vec_cte.c.chunk_id == kw_cte.c.chunk_id,
                full=True,
            )
        )
        .order_by(sa_text("rrf_score DESC"))
        .limit(top_k)
    )

    rows = (await session.execute(fused)).all()
    results = []
    for r in rows:
        results.append({
            "content": r.content,
            "document_name": r.document_name,
            "position": r.position,
            "score": float(r.rrf_score) if r.rrf_score is not None else 0.0,
        })
    return results


async def list_documents(session: AsyncSession) -> list[dict[str, Any]]:
    """列出所有文档及其分块数。"""
    stmt = (
        select(
            DocumentConfig.id,
            DocumentConfig.name,
            DocumentConfig.source_type,
            DocumentConfig.created_at,
            func.count(DocumentChunk.id).label("chunk_count"),
        )
        .outerjoin(DocumentChunk, DocumentChunk.document_id == DocumentConfig.id)
        .group_by(DocumentConfig.id)
        .order_by(DocumentConfig.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "source_type": r.source_type,
            "created_at": r.created_at,
            "chunk_count": r.chunk_count,
        }
        for r in rows
    ]


async def get_document(session: AsyncSession, doc_id: int) -> DocumentConfig | None:
    return await session.get(DocumentConfig, doc_id)


async def delete_document(session: AsyncSession, doc_id: int) -> bool:
    """删除文档（级联删除分块）。"""
    doc = await session.get(DocumentConfig, doc_id)
    if doc is None:
        return False
    await session.delete(doc)
    await session.commit()
    return True


async def count_chunks(session: AsyncSession, doc_id: int) -> int:
    stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == doc_id)
    return int((await session.execute(stmt)).scalar() or 0)


async def search_kb_high_confidence(
    query_vec: list[float],
    top_k: int = 2,
    score_threshold: float = 0.65,
) -> list[dict[str, Any]]:
    """纯语义检索（不用 hybrid），返回相似度 > threshold 的 top_k。

    用于 kickoff 前预注入相关知识库片段到 task description。
    cosine distance < (1 - threshold) 视为高置信。
    返回 [{"content", "document_name", "score"}]，score = 1 - distance。
    """
    if not query_vec:
        return []
    try:
        from app.db.session import AsyncSessionLocal
        vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"
        # 先取 3 倍候选，再按距离阈值过滤
        pool = top_k * 3
        stmt = sa_text(
            """
            SELECT dc.content, dc.position,
                   dc.embedding <=> CAST(:vec AS vector) AS distance,
                   doc.name AS document_name
            FROM document_chunks dc
            JOIN document_configs doc ON dc.document_id = doc.id
            ORDER BY dc.embedding <=> CAST(:vec AS vector)
            LIMIT :pool
            """
        )
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                stmt, {"vec": vec_str, "pool": pool}
            )).fetchall()

        max_distance = 1.0 - score_threshold
        results = []
        for r in rows:
            dist = float(r[2])
            if dist < max_distance:
                results.append({
                    "content": r[0],
                    "position": r[1],
                    "score": 1.0 - dist,
                    "document_name": r[3],
                })
            if len(results) >= top_k:
                break
        return results
    except Exception as e:
        logger.warning(f"search_kb_high_confidence failed: {e}")
        return []

