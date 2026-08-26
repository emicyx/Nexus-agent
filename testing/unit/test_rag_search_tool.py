"""RagSearchTool 单测（2026-08-26 修复项）。

- top_k 参数化：schema 默认值（5）= LLM 未传参 → 用 top_k_default；
  显式传值生效；解析失败/越界回退 top_k_default
- 输出格式带 document_id + position（Agent 二段检索的依据）
- 空结果提示
"""
import app.tools.rag_search_tool as rst
from app.tools.rag_search_tool import RagSearchTool


def _make_tool(top_k_default=10):
    return RagSearchTool(top_k_default=top_k_default)


def _capture_search(monkeypatch):
    calls = []

    def fake_search(query, top_k, document_id=None):
        calls.append({"query": query, "top_k": top_k, "document_id": document_id})
        return [
            {
                "content": "正文内容甲",
                "document_name": "02-数据英雄.md",
                "document_id": 7,
                "position": 2,
                "score": 0.033,
            }
        ]

    monkeypatch.setattr(rst, "_search_sync", fake_search)
    return calls


# ---------- top_k 参数化（P0-1） ----------

def test_top_k_schema_default_uses_top_k_default(monkeypatch):
    """LLM 未显式传 top_k（Pydantic 填 schema 默认 5）→ 用配置值 10。"""
    calls = _capture_search(monkeypatch)
    tool = _make_tool(top_k_default=10)
    tool._run("如何制作新英雄")
    assert calls[-1]["top_k"] == 10


def test_top_k_configured_value_respected(monkeypatch):
    """config_json 配了 3：未传参时生效 3（而非硬编码 10）。"""
    calls = _capture_search(monkeypatch)
    tool = _make_tool(top_k_default=3)
    tool._run("如何制作新英雄")
    assert calls[-1]["top_k"] == 3


def test_top_k_explicit_value_wins(monkeypatch):
    """LLM 显式传 7 → 用 7。"""
    calls = _capture_search(monkeypatch)
    tool = _make_tool(top_k_default=10)
    tool._run("如何制作新英雄", top_k=7)
    assert calls[-1]["top_k"] == 7


def test_top_k_parse_failure_falls_back(monkeypatch):
    calls = _capture_search(monkeypatch)
    tool = _make_tool(top_k_default=10)
    tool._run("如何制作新英雄", top_k="abc")
    assert calls[-1]["top_k"] == 10


def test_top_k_out_of_range_falls_back(monkeypatch):
    calls = _capture_search(monkeypatch)
    tool = _make_tool(top_k_default=10)
    tool._run("如何制作新英雄", top_k=99)
    assert calls[-1]["top_k"] == 10


# ---------- 输出格式（P0-2） ----------

def test_output_contains_document_id_and_position(monkeypatch):
    _capture_search(monkeypatch)
    tool = _make_tool()
    out = tool._run("如何制作新英雄")
    assert "document_id=7" in out
    assert "position=2" in out
    assert '[02-数据英雄.md]' in out
    assert 'document_id="7"' in out  # kb_content 标签属性


def test_output_mentions_followup_hint(monkeypatch):
    """输出需提示 Agent 可用 document_id 做二段检索。"""
    _capture_search(monkeypatch)
    tool = _make_tool()
    out = tool._run("如何制作新英雄")
    assert "document_id" in out and "top_k" in out


def test_empty_results_message(monkeypatch):
    monkeypatch.setattr(rst, "_search_sync", lambda q, k, document_id=None: [])
    tool = _make_tool()
    out = tool._run("无关问题")
    assert "知识库为空或未找到相关内容" in out
