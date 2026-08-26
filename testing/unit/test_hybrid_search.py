"""混合检索共享模块单测（2026-08-26 修复：结果带 document_id + 预注入排除导航块）。"""
from types import SimpleNamespace

from app.services.hybrid_search import HYBRID_SQL, map_rows, build_tsquery
from app.services.document_service import _KB_PREINJECT_SQL


def test_hybrid_sql_selects_document_id():
    # 两条 CTE 与最终 SELECT 都要带 document_id（Agent 二段检索依赖）
    assert HYBRID_SQL.text.count("document_id") >= 5


def test_map_rows_includes_document_id():
    row = SimpleNamespace(
        chunk_id=1,
        content="正文",
        position=2,
        document_id=7,
        document_name="02-数据英雄.md",
        rrf_score=0.033,
    )
    out = map_rows([row])
    assert out[0]["document_id"] == 7
    assert out[0]["position"] == 2
    assert out[0]["document_name"] == "02-数据英雄.md"
    assert abs(out[0]["score"] - 0.033) < 1e-9


def test_map_rows_handles_null_score():
    row = SimpleNamespace(
        chunk_id=1, content="c", position=0, document_id=1,
        document_name="d", rrf_score=None,
    )
    assert map_rows([row])[0]["score"] == 0.0


def test_preinject_sql_excludes_nav_chunks():
    """预注入 SQL 排除多块文档的 position=0 导航块，单块文档除外。"""
    sql = _KB_PREINJECT_SQL.text
    assert "dc.position > 0" in sql
    assert "COUNT(*)" in sql  # 单块文档豁免


def test_build_tsquery_or_fallback():
    assert build_tsquery(["英雄", "创建"]) == "英雄 | 创建"
    assert build_tsquery([]) == "zzzz_nomatch"
