"""路由 v0 单测：/cmd 前缀解析 + 默认 crew 决策（纯确定性零 LLM）。

R0 契约：route_message 是纯函数——不查 DB、不调 LLM、同一输入同一输出。
命令→crew 映射的权威表在 backend/app/crews/route_v0.py。
"""
import pytest

from app.crews.route_v0 import (
    COMMAND_CREW_MAP,
    DEFAULT_CREW_NAME,
    RouteDecision,
    available_commands,
    route_message,
)


class TestDefaultRouting:
    def test_plain_message_routes_default_crew(self):
        d = route_message("如何部署 Nexus？")
        assert d == RouteDecision(
            crew_name=DEFAULT_CREW_NAME, command=None, message="如何部署 Nexus？"
        )
        assert d.crew_name == "researcher_writer"

    def test_message_without_slash_prefix_unaffected(self):
        # 非行首的斜杠（如 URL）不触发命令解析
        d = route_message("帮我抓取 https://example.com 的内容")
        assert d.command is None
        assert d.error is None
        assert d.message == "帮我抓取 https://example.com 的内容"

    def test_leading_whitespace_then_plain_text(self):
        d = route_message("  普通问题  ")
        assert d.command is None
        assert d.crew_name == DEFAULT_CREW_NAME

    def test_path_like_text_not_command(self):
        # /etc/hosts 形态：token 含多段斜杠，不匹配单 token 命令 → 默认路由原样透传
        d = route_message("/etc/hosts 里有什么")
        assert d.command is None
        assert d.crew_name == DEFAULT_CREW_NAME
        assert d.message == "/etc/hosts 里有什么"


class TestCommandRouting:
    @pytest.mark.parametrize(
        ("cmd", "crew"),
        [
            ("/kb", "knowledge_qa"),
            ("/write", "iterative_write_crew"),
            ("/ingest", "web_ingest_crew"),
        ],
    )
    def test_known_command_routes_and_strips_prefix(self, cmd, crew):
        d = route_message(f"{cmd} 如何配置混合检索？")
        assert d.crew_name == crew
        assert d.command == cmd
        assert d.message == "如何配置混合检索？"
        assert d.error is None

    def test_command_case_insensitive(self):
        d = route_message("/KB 知识库里有什么")
        assert d.crew_name == "knowledge_qa"
        assert d.command == "/kb"
        assert d.message == "知识库里有什么"

    def test_command_with_leading_whitespace(self):
        d = route_message("   /write 写一份周报")
        assert d.crew_name == "iterative_write_crew"
        assert d.message == "写一份周报"

    def test_command_with_hyphen_underscore_token(self):
        # 命令 token 允许 - _，但未注册 → 未知命令
        d = route_message("/my-cmd 内容")
        assert d.error is not None
        assert "/my-cmd" in d.error

    def test_extra_whitespace_after_command(self):
        d = route_message("/kb    多空格问题")
        assert d.message == "多空格问题"


class TestCommandErrors:
    def test_unknown_command_returns_readable_error(self):
        d = route_message("/foo 帮我做事")
        assert d.error is not None
        assert "未知命令 /foo" in d.error
        # 错误信息列出全部可用命令（用户纠错入口）
        for cmd in available_commands():
            assert cmd in d.error

    def test_bare_command_without_content_is_error(self):
        d = route_message("/kb")
        assert d.error is not None
        assert "/kb" in d.error
        assert "缺少内容" in d.error

    def test_bare_command_with_trailing_whitespace_is_error(self):
        d = route_message("/kb   ")
        assert d.error is not None


class TestPurity:
    def test_deterministic_and_llm_free(self):
        # 同一输入重复调用结果一致（纯函数）；无 DB/LLM 依赖由 import 静态保证
        msg = "/write 写一份部署手册"
        assert route_message(msg) == route_message(msg)

    def test_map_covers_only_retained_crews(self):
        # 映射不得指向退役 crew（R0 目录：team_orchestrator/safety_check 已删）
        retired = {"team_orchestrator", "safety_check"}
        assert not retired & set(COMMAND_CREW_MAP.values())
        assert not retired & {DEFAULT_CREW_NAME}
