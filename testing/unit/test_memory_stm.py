"""短期记忆压缩单测（backend/app/services/memory_stm.py）。"""
from app.config import settings
from app.services.memory_stm import (
    MAX_HISTORY_TURNS,
    MAX_PER_MESSAGE_CHARS,
    MAX_TOTAL_CHARS,
    _get_summarizer_llm,
    build_history_context,
    compress_history,
    select_expired_batch,
    summarize_text,
)


def _msgs(n: int, content_len: int = 10) -> list[dict[str, str]]:
    """构造 n 条 user/assistant 交替消息。"""
    out = []
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": "字" * content_len})
    return out


def test_empty_input():
    assert compress_history([]) == []
    assert build_history_context([]) == ""


def test_sliding_window_keeps_last_6():
    msgs = _msgs(10)
    out = compress_history(msgs)
    assert len(out) == MAX_HISTORY_TURNS  # 6
    # 保留的是最后 6 条
    assert out[-1] == msgs[-1]
    assert out[0] == msgs[-MAX_HISTORY_TURNS]


def test_single_message_truncation():
    msgs = [{"role": "user", "content": "长" * 2000}]  # 2000 字
    out = compress_history(msgs)
    assert len(out[0]["content"]) == MAX_PER_MESSAGE_CHARS  # 500


def test_total_cap_removes_oldest_keeps_two():
    # 每条 600 字，6 条总长 3600 > 3000 → 需删到 ≤3000 且至少留 2
    msgs = _msgs(6, content_len=600)
    out = compress_history(msgs)
    total = sum(len(m["content"]) for m in out)
    assert total <= MAX_TOTAL_CHARS
    assert len(out) >= 2


def test_build_history_context_labels():
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你"},
    ]
    ctx = build_history_context(msgs)
    assert "以下是之前的对话记录" in ctx
    assert "用户: 你好" in ctx
    assert "助手: 你好，有什么可以帮你" in ctx
    assert "用户最新问题" in ctx


# ---------- 滚动摘要：select_expired_batch ----------

def _msgs_with_id(n: int) -> list[dict]:
    """构造 id=1..n 的 user/assistant 交替消息。"""
    return [
        {"id": i, "role": "user" if i % 2 == 1 else "assistant", "content": f"msg{i}"}
        for i in range(1, n + 1)
    ]


def test_select_expired_batch_basic():
    # 12 条，窗口 6 → beyond = 1..6；last=4 → 增量 batch = [5, 6]
    batch = select_expired_batch(_msgs_with_id(12), last_message_id=4)
    assert [m["id"] for m in batch] == [5, 6]


def test_select_expired_batch_under_window():
    # 未超窗口 → 无过期消息
    assert select_expired_batch(_msgs_with_id(6), last_message_id=0) == []


def test_select_expired_batch_no_repeat():
    # 14 条窗口 6 → beyond = 1..8；last=6 → batch = [7, 8]（幂等，不复述已摘要的 1..6）
    batch = select_expired_batch(_msgs_with_id(14), last_message_id=6)
    assert [m["id"] for m in batch] == [7, 8]


# ---------- 滚动摘要：build_history_context 带 summary ----------

def test_build_history_context_with_summary():
    msgs = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好"}]
    ctx = build_history_context(msgs, summary="用户偏好中文回答")
    assert "更早对话的摘要" in ctx
    assert "用户偏好中文回答" in ctx
    assert "以下是之前的对话记录" in ctx  # 窗口部分仍在


def test_build_history_context_summary_capped():
    long_summary = "长" * (settings.STM_SUMMARY_MAX_CHARS + 200)
    ctx = build_history_context([], summary=long_summary)
    assert "更早对话的摘要" in ctx
    # 摘要段被截断到上限 + 省略号
    assert "长" * settings.STM_SUMMARY_MAX_CHARS in ctx
    assert ctx.count("长") <= settings.STM_SUMMARY_MAX_CHARS + 10


# ---------- 滚动摘要：summarize_text（mock LLM，无网络） ----------

def test_summarize_text_merges(monkeypatch):
    calls = {}

    class FakeLLM:
        def call(self, messages, **kwargs):
            calls["messages"] = messages
            return "合并后的摘要：偏好中文回答；决定明天提交"

    fake = FakeLLM()
    monkeypatch.setattr(
        "app.services.memory_stm._get_summarizer_llm", lambda: fake
    )
    # 验证 monkeypatch 生效（从模块重新取，避免导入时的旧引用）
    import app.services.memory_stm as _stm_module
    assert _stm_module._get_summarizer_llm() is fake

    out = summarize_text("偏好中文回答", "用户: 明天提交\n助手: 好的")
    assert out == "合并后的摘要：偏好中文回答；决定明天提交"
    assert calls["messages"][0]["role"] == "system"
    assert "已有摘要" in calls["messages"][1]["content"]
    assert "偏好中文回答" in calls["messages"][1]["content"]
    assert "明天提交" in calls["messages"][1]["content"]
