"""RRF 混合检索共享模块（向量 + 关键词融合）

document_service.search_documents（API 异步路径）与 rag_search_tool
（CrewAI 同步工具路径）共用同一份 SQL、参数与结果映射，避免两处实现漂移。
两条路径只差 Session 的 sync/async，lexeme 预查询由调用方用各自的 Session 执行。
"""
from typing import Any

from sqlalchemy import text as sa_text

from app.services.keyword_search import build_or_tsquery

# RRF 融合参数
RRF_K = 60
# 每路预取上限（控制扫描成本）
RRF_POOL = 200

# 关键词路预处理：zhparser 切词取 lexeme（修复 plainto_tsquery 全 AND 零命中问题）
LEXEME_SQL = sa_text("SELECT lexeme FROM unnest(to_tsvector('chinese', :q))")

# 混合检索 SQL：向量路 + 关键词路 RRF 融合
# 注意 1：关键词路必须用 to_tsquery('chinese', :tsq) 生成 tsquery，
# 不能直接把原始查询字符串传给 @@（会被隐式当作 tsquery 解析，中文+空格会报语法错误）
# 注意 2：:doc_id 必须显式 CAST —— doc_id=None 时 asyncpg 无法推断裸 NULL 的类型
# （AmbiguousParameterError），CAST 后新连接首次执行也能通过
HYBRID_SQL = sa_text("""
WITH vec AS (
    SELECT dc.id AS chunk_id, dc.content AS content, dc.position AS position,
           doc.name AS document_name,
           ROW_NUMBER() OVER (ORDER BY dc.embedding <=> CAST(:query_vec AS vector)) AS rn
    FROM document_chunks dc
    JOIN document_configs doc ON dc.document_id = doc.id
    WHERE (CAST(:doc_id AS int) IS NULL OR dc.document_id = :doc_id)
    ORDER BY dc.embedding <=> CAST(:query_vec AS vector)
    LIMIT :pool
),
kw AS (
    SELECT dc.id AS chunk_id, dc.content AS content, dc.position AS position,
           doc.name AS document_name,
           ROW_NUMBER() OVER (ORDER BY ts_rank(dc.tsv, to_tsquery('chinese', :tsq)) DESC) AS rn
    FROM document_chunks dc
    JOIN document_configs doc ON dc.document_id = doc.id
    WHERE dc.tsv @@ to_tsquery('chinese', :tsq)
      AND (CAST(:doc_id AS int) IS NULL OR dc.document_id = :doc_id)
    ORDER BY ts_rank(dc.tsv, to_tsquery('chinese', :tsq)) DESC
    LIMIT :pool
)
SELECT COALESCE(vec.chunk_id, kw.chunk_id) AS chunk_id,
       COALESCE(vec.content, kw.content) AS content,
       COALESCE(vec.position, kw.position) AS position,
       COALESCE(vec.document_name, kw.document_name) AS document_name,
       ( COALESCE(1.0 / (:k + vec.rn), 0.0)
         + COALESCE(1.0 / (:k + kw.rn), 0.0) ) AS rrf_score
FROM vec
FULL OUTER JOIN kw ON vec.chunk_id = kw.chunk_id
ORDER BY rrf_score DESC
LIMIT :top_k
""")


def build_tsquery(lexemes: list[str]) -> str:
    """lexeme 列表 → OR tsquery；无内容词时返回永不匹配的占位。"""
    return build_or_tsquery(lexemes) or "zzzz_nomatch"


def hybrid_params(
    vec_literal: str,
    tsq: str,
    document_id: int | None,
    top_k: int,
) -> dict[str, Any]:
    """HYBRID_SQL 的绑定参数（两路调用方保持一致）。"""
    return {
        "query_vec": vec_literal,
        "tsq": tsq,
        "doc_id": document_id,
        "pool": RRF_POOL,
        "k": RRF_K,
        "top_k": top_k,
    }


def map_rows(rows) -> list[dict[str, Any]]:
    """HYBRID_SQL 结果行 → [{content, document_name, position, score}]。"""
    return [
        {
            "content": r.content,
            "document_name": r.document_name,
            "position": r.position,
            "score": float(r.rrf_score) if r.rrf_score is not None else 0.0,
        }
        for r in rows
    ]
