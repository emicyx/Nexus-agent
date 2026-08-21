"""SSE 事件契约测试：后端事件全集 ↔ 前端类型定义 + switch 消费。

后端事件类型是裸字符串（core/events.py + factory.py 推送点），前端
api-client.ts 的 ChatEvent 联合类型与 use-chat.ts 的 switch 各手工镜像一份，
历史上发生过"类型定义了但 switch 没有 case"的漂移（task_completed/delegation
被静默丢弃）。本测试锁死三方对齐：
  1. api-client.ts 必须为每个后端事件定义 `{ type: "<name>" }`；
  2. use-chat.ts 必须有对应 `case "<name>"`。

新增后端事件时：先改 BACKEND_EVENT_TYPES，测试会逼你同步前端。
"""
import re
from pathlib import Path

# 后端事件全集（以 backend/app/core/events.py 文档字符串 + factory.py 推送点为准）
BACKEND_EVENT_TYPES = {
    "agent_thinking",
    "thinking_token",
    "tool_call",
    "tool_result",
    "approval_requested",
    "token",
    "final_answer",
    "task_completed",
    "delegation",
    "error",
    "done",
}

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_API_CLIENT = _FRONTEND / "lib" / "api-client.ts"
_USE_CHAT = _FRONTEND / "hooks" / "use-chat.ts"


def test_api_client_defines_all_backend_events():
    src = _API_CLIENT.read_text(encoding="utf-8")
    for evt in BACKEND_EVENT_TYPES:
        assert f'type: "{evt}"' in src, (
            f"后端事件 {evt} 未在 api-client.ts 的 ChatEvent 联合类型中定义"
        )


def test_use_chat_handles_all_backend_events():
    src = _USE_CHAT.read_text(encoding="utf-8")
    handled = set(re.findall(r'case "([a-z_]+)"', src))
    missing = BACKEND_EVENT_TYPES - handled
    assert not missing, (
        f"后端事件 {sorted(missing)} 在 use-chat.ts 的 switch 中没有 case，"
        f"事件将被静默丢弃"
    )


def test_use_chat_has_no_extra_cases():
    """前端 case 不在後端全集内 = 死代码或拼错，同样要修。"""
    src = _USE_CHAT.read_text(encoding="utf-8")
    handled = set(re.findall(r'case "([a-z_]+)"', src))
    extra = handled - BACKEND_EVENT_TYPES
    assert not extra, f"use-chat.ts 存在后端不发的事件 case: {sorted(extra)}"
