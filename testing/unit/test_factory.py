"""Crew 工厂纯函数单测（backend/app/crews/factory.py）。

只测无副作用/无 DB 依赖的纯函数。
"""
import app.crews.factory as factory_mod
from app.crews.factory import _build_embedder_config, _substitute_user_input


def test_substitute_basic():
    assert _substitute_user_input("问题：{user_input}", "你好") == "问题：你好"


def test_substitute_multiple_occurrences():
    tpl = "{user_input} vs {user_input}"
    assert _substitute_user_input(tpl, "A") == "A vs A"


def test_substitute_other_braces_preserved():
    # str.format 会因 {name} 报 KeyError，replace 不会
    tpl = "任务是 {user_input}，使用 {{保留}} 和 {not_a_placeholder}"
    out = _substitute_user_input(tpl, "X")
    assert out == "任务是 X，使用 {{保留}} 和 {not_a_placeholder}"


def test_substitute_empty_template():
    assert _substitute_user_input("", "你好") == ""
    # 模板没有占位符时原样返回
    assert _substitute_user_input("无占位符", "你好") == "无占位符"


def test_build_embedder_config(monkeypatch):
    # 重置模块级缓存，保证读取最新 settings
    monkeypatch.setattr(factory_mod, "_embedder_config_cache", None)
    cfg = _build_embedder_config()
    assert cfg["provider"] == "openai"
    assert "dashscope" in cfg["config"]["api_base"]
    assert "api_key" in cfg["config"] and cfg["config"]["api_key"]
    # 单例：两次调用返回同一 dict
    assert _build_embedder_config() is cfg
