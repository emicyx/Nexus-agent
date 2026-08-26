"""工具注册表单测（backend/app/crews/tool_registry.py）。"""
import pytest

from app.crews.tool_registry import TOOL_OPTIONS, TOOL_REGISTRY, instantiate_tool


def test_registry_contains_core_tools():
    # 核心工具必须在注册表
    for key in ("rag_search", "baidu_search", "human_approval", "fetch_url", "kb_ingest", "write_markdown"):
        assert key in TOOL_REGISTRY


def test_options_are_serializable():
    assert isinstance(TOOL_OPTIONS, list)
    assert all({"key", "label"} <= set(o) for o in TOOL_OPTIONS)
    assert len(TOOL_OPTIONS) == len(TOOL_REGISTRY)


def test_instantiate_rag_search_with_config():
    tool = instantiate_tool("rag_search", {"top_k": 3})
    assert tool.top_k_default == 3


def test_instantiate_rag_search_default():
    # 默认 10（2026-08-02 RAG 报告 P0-1：top5→top10 证据可见率 50%→71%）
    tool = instantiate_tool("rag_search")
    assert tool.top_k_default == 10


def test_instantiate_baidu_search_with_config():
    tool = instantiate_tool("baidu_search", {"max_results": 8})
    assert tool.max_results == 8


def test_unknown_tool_raises():
    with pytest.raises(KeyError):
        instantiate_tool("not_a_real_tool")


def test_noarg_tool_constructs():
    # intermediate 是无参工具
    tool = instantiate_tool("intermediate")
    assert tool is not None
    # 工具自带 name（类默认值，可能与注册 key 不同）
    assert getattr(tool, "name", None)
