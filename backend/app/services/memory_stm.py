"""短期记忆（会话内）压缩与格式化。

从 DB chat_messages 加载的历史消息可能很长，直接注入 task description 会爆 LLM 上下文窗口。
本模块提供滑动窗口 + 单条截断 + 总量兜底的三级压缩。
"""
import logging

logger = logging.getLogger("memory.stm")

# 保留最近 N 条消息（每轮 Q+A = 2 条，6 条 = 3 轮）
MAX_HISTORY_TURNS = 6
# 单条消息最大字符数
MAX_PER_MESSAGE_CHARS = 500
# history_context 前缀总字符上限（≈ 750 token，留足 LLM 上下文空间）
MAX_TOTAL_CHARS = 3000


def compress_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """滑动窗口 + 单条截断 + 总量兜底。

    1. 滑窗：只取最近 MAX_HISTORY_TURNS 条
    2. 单条截断：每条 content 限 MAX_PER_MESSAGE_CHARS
    3. 总量兜底：若总长 > MAX_TOTAL_CHARS，从最旧的开始删，直到达标（至少保留 2 条）
    """
    if not messages:
        return []
    # 1. 滑窗
    recent = messages[-MAX_HISTORY_TURNS:]
    # 2. 单条截断
    truncated = [
        {"role": m["role"], "content": m["content"][:MAX_PER_MESSAGE_CHARS]}
        for m in recent
    ]
    # 3. 总量兜底
    while (
        sum(len(m["content"]) for m in truncated) > MAX_TOTAL_CHARS
        and len(truncated) > 2
    ):
        truncated.pop(0)
    return truncated


def build_history_context(messages: list[dict[str, str]]) -> str:
    """格式化历史消息为 task description 前缀。

    先 compress_history 压缩，再拼成可读字符串。
    """
    compressed = compress_history(messages)
    if not compressed:
        return ""
    lines = ["以下是之前的对话记录，请参考上下文回答用户最新问题：\n"]
    for msg in compressed:
        role_label = "用户" if msg["role"] == "user" else "助手"
        lines.append(f"{role_label}: {msg['content']}")
    lines.append("\n--- 以上为历史记录，以下是用户最新问题 ---\n")
    return "\n".join(lines)
