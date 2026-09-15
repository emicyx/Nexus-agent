"""路由 v0：/cmd 前缀解析 + 默认 crew 决策（R0，纯确定性、零 LLM）。

Auto 模式（POST /v1/chat/stream 的 mode="auto"）下：
- 消息以已知 /cmd 开头 → 路由到对应 crew，命令前缀从送入 crew 的消息中剥离；
- 无命令前缀 → 默认 crew（researcher_writer，助手 shell 默认入口）；
- 未知命令 / 命令后无内容 → 返回用户可读错误（由 API 层转 400，不进 LLM）。

命令 → crew 映射是唯一权威表；前端 / 命令列表（frontend/src/lib/commands.ts）
与此镜像同步。新增命令时两处一起改（S1 的 /ops 即如此）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 命令 → crew 名（seed.py 的 crew 目录）
COMMAND_CREW_MAP: dict[str, str] = {
    "/kb": "knowledge_qa",
    "/write": "iterative_write_crew",
    "/ingest": "web_ingest_crew",
}

DEFAULT_CREW_NAME = "researcher_writer"

# 行首命令 token：/word，word 限字母数字_ -（防止把普通路径 "/etc/hosts 读取" 误判）
_COMMAND_TOKEN_RE = re.compile(r"^(/[A-Za-z0-9_-]+)(?:\s+|$)")


@dataclass(frozen=True)
class RouteDecision:
    """路由决策结果（纯数据，无副作用）。"""

    crew_name: str
    command: str | None  # 命中的命令（如 "/kb"）；默认路由为 None
    message: str         # 送入 crew 的消息（已剥离命令前缀）
    error: str | None = None  # 非空时为用户可读错误，此时其余字段无意义


def available_commands() -> list[str]:
    """当前可用命令（排序稳定，供错误提示与测试断言）。"""
    return sorted(COMMAND_CREW_MAP)


def route_message(message: str) -> RouteDecision:
    """解析用户消息，决定服务的 crew。

    确定性纯函数：同一输入永远同一输出，不查 DB、不调 LLM。
    """
    text = message.lstrip()
    m = _COMMAND_TOKEN_RE.match(text)
    if not m:
        return RouteDecision(crew_name=DEFAULT_CREW_NAME, command=None, message=message)

    cmd = m.group(1).lower()
    crew_name = COMMAND_CREW_MAP.get(cmd)
    if crew_name is None:
        valid = " ".join(available_commands())
        return RouteDecision(
            crew_name="",
            command=cmd,
            message="",
            error=f"未知命令 {cmd}。可用命令：{valid}；直接输入问题则由默认助手回答。",
        )
    rest = text[m.end():].strip()
    if not rest:
        return RouteDecision(
            crew_name="",
            command=cmd,
            message="",
            error=f"命令 {cmd} 后缺少内容，请在命令后写下你的问题。",
        )
    return RouteDecision(crew_name=crew_name, command=cmd, message=rest)
