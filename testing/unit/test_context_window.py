"""C1 上下文窗口查表单测（backend/app/llm/aliyun_llm.py get_context_window_size）。

原实现全家族硬编码 8192，qwen-plus 实际 131072——错误元数据会误导
CrewAI 的上下文裁剪，长对话被过早截断。
"""
import pytest

from app.config import settings
from app.llm.aliyun_llm import AliyunLLM


def _llm(model: str) -> AliyunLLM:
    return AliyunLLM(model=model, api_key="sk-test")


@pytest.mark.parametrize(
    "model,expected",
    [
        ("qwen-plus", 131_072),
        ("qwen3-plus", 131_072),
        ("qwen-max", 131_072),
        ("qwen3-max", 131_072),
        ("qwen-turbo", 1_000_000),
        ("qwen3-turbo", 1_000_000),
        ("qwen-flash", 1_000_000),
        ("qwen-long", 1_000_000),
        ("some-unknown-model", 131_072),  # 未知模型不再掉回 8192
    ],
)
def test_context_window_table(model, expected):
    assert _llm(model).get_context_window_size() == expected


def test_env_override_wins(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW_OVERRIDE", 55_555)
    assert _llm("qwen-plus").get_context_window_size() == 55_555
    assert _llm("anything").get_context_window_size() == 55_555


def test_case_insensitive_match():
    assert _llm("QWEN-PLUS").get_context_window_size() == 131_072
