"""短期记忆（会话内）压缩与格式化。

从 DB chat_messages 加载的历史消息可能很长，直接注入 task description 会爆 LLM 上下文窗口。
本模块提供滑动窗口 + 单条截断 + 总量兜底的三级压缩，并对滑出窗口的旧消息做**增量滚动摘要**
（后台 qwen-turbo，fire-and-forget，不阻塞聊天响应），使早期上下文（偏好/事实/决策/进行中任务）得以保留。
"""
import concurrent.futures
import logging
import time

from app.config import settings

logger = logging.getLogger("memory.stm")

# 保留最近 N 条消息（每轮 Q+A = 2 条，6 条 = 3 轮）
MAX_HISTORY_TURNS = 6
# 单条消息最大字符数
MAX_PER_MESSAGE_CHARS = 500
# history_context 前缀总字符上限（≈ 750 token，留足 LLM 上下文空间）
MAX_TOTAL_CHARS = 3000

# ---------- 滚动摘要：后台线程 + LLM 单例 ----------
_stm_summary_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="stm-summary",
)
_stm_summarizer_llm = None  # AliyunLLM 单例，延迟初始化

STM_SUMMARY_PROMPT = (
    "你是一个对话记忆压缩器。请把「新增对话」合并进「已有摘要」，产出一份更新后的会话滚动摘要。\n"
    "要求：\n"
    "1. 保留：用户明确表达的偏好、已确认的事实、关键决策、进行中的任务/需求、重要承诺。\n"
    "2. 丢弃：寒暄、客套、一次性细节、重复表述。\n"
    "3. 摘要用中文、简洁连贯，按主题组织，可覆盖多轮内容。\n"
    "4. 若「已有摘要」为空，则直接从「新增对话」生成摘要。\n"
    "5. 只输出摘要正文，不要任何标题、序号或解释。"
)


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


def build_history_context(
    messages: list[dict[str, str]], summary: str = "",
) -> str:
    """格式化历史消息为 task description 前缀。

    先 compress_history 压缩（窗口逐字），若有滚动摘要则在窗口前加摘要段。
    summary 参数默认空串，行为与旧版完全一致（既有调用方/测试不受影响）。
    """
    compressed = compress_history(messages)
    if not compressed and not summary:
        return ""
    parts = []
    if summary:
        max_chars = getattr(settings, "STM_SUMMARY_MAX_CHARS", 1200)
        s = summary if len(summary) <= max_chars else summary[:max_chars] + "…"
        parts.append(f"以下是更早对话的摘要：\n{s}\n")
    if compressed:
        lines = ["以下是之前的对话记录，请参考上下文回答用户最新问题：\n"]
        for msg in compressed:
            role_label = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{role_label}: {msg['content']}")
        lines.append("\n--- 以上为历史记录，以下是用户最新问题 ---\n")
        parts.append("\n".join(lines))
    return "\n".join(parts)


# ---------- 滚动摘要：增量合并（仅 worker 线程内调用，绝不在事件循环） ----------

def _get_summarizer_llm():
    """摘要专用 LLM（默认 qwen-turbo），与主回答 LLM 隔离。"""
    global _stm_summarizer_llm
    if _stm_summarizer_llm is None:
        from app.llm.aliyun_llm import AliyunLLM
        model = getattr(settings, "STM_SUMMARY_LLM_MODEL", "qwen-turbo")
        _stm_summarizer_llm = AliyunLLM(
            model=model,
            api_key=settings.QWEN_API_KEY,
            region=settings.LLM_REGION,
            temperature=0.2,
            timeout=60,
        )
    return _stm_summarizer_llm


def select_expired_batch(
    messages: list[dict], last_message_id: int, window: int = MAX_HISTORY_TURNS,
) -> list[dict]:
    """取「已滑出窗口」且「id > last_message_id」的增量 batch（幂等）。

    messages 按 id 升序。返回值为 beyond-window 子集中的最后 window 条，
    约束首跑/长会话的大 batch；更早的剩余消息由后续轮次补摘。
    """
    if not messages or len(messages) <= window:
        return []
    beyond = messages[:-window] if window > 0 else messages
    batch = [m for m in beyond if m["id"] > last_message_id]
    return batch[-window:]


def format_batch(batch: list[dict]) -> str:
    """把 batch 格式化为「用户:/助手:」行，与读取路径标签一致。"""
    lines = []
    for m in batch:
        role_label = "用户" if m["role"] == "user" else "助手"
        lines.append(f"{role_label}: {m['content'][:MAX_PER_MESSAGE_CHARS]}")
    return "\n".join(lines)


def summarize_text(prev_summary: str, new_text: str) -> str:
    """合并已有摘要与新消息为滚动摘要（同步 LLM，仅 worker 线程内调用）。"""
    user_prompt = (
        f"已有摘要：\n{prev_summary}\n\n新增对话：\n{new_text}"
        if prev_summary
        else f"新增对话：\n{new_text}"
    )
    llm = _get_summarizer_llm()
    result = llm.call(
        messages=[
            {"role": "system", "content": STM_SUMMARY_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    )
    text = (result or "").strip()
    if not text:
        return ""
    # 简单清理可能的 markdown 围栏（如 ```text ... ```）
    if text.startswith("```"):
        text = text.strip("`").strip()
        # 若围栏带语言标签（如 ```text），去掉首行语言标签
        first_nl = text.find("\n")
        if first_nl != -1 and len(text[:first_nl]) <= 10:
            text = text[first_nl + 1 :].strip()
    return text


def summarize_session_async(session_id: int) -> None:
    """后台线程 fire-and-forget：刷新会话滚动摘要。"""
    try:
        _stm_summary_executor.submit(_run_summary_refresh, session_id)
    except Exception as e:
        logger.warning(f"stm summary submit failed: {e}")


def _run_summary_refresh(session_id: int) -> None:
    """后台线程：把滑出窗口且未摘要的消息增量合并进滚动摘要（幂等、自愈）。"""
    t0 = time.perf_counter()
    try:
        from sqlalchemy import select

        from app.models import ChatMessage, ChatSessionSummary
        from app.services.memory_ltm import _get_sync_session

        with _get_sync_session() as db:
            row = db.execute(
                select(ChatSessionSummary).where(
                    ChatSessionSummary.session_id == session_id
                )
            ).scalar_one_or_none()
            last_id = row.last_message_id if row else 0
            prev_summary = (row.summary or "") if row else ""

            msgs = db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id)
            ).scalars().all()
            messages = [
                {"id": m.id, "role": m.role, "content": m.content} for m in msgs
            ]
            batch = select_expired_batch(messages, last_id)
            if not batch:
                return  # 无新滑出消息，不调 LLM

            new_text = format_batch(batch)
            new_summary = summarize_text(prev_summary, new_text)
            if not new_summary:
                logger.warning(
                    "stm summary: empty output, skip (session_id=%s)", session_id
                )
                return

            max_id = batch[-1]["id"]
            if row is None:
                db.add(
                    ChatSessionSummary(
                        session_id=session_id,
                        summary=new_summary,
                        last_message_id=max_id,
                    )
                )
            else:
                row.summary = new_summary
                row.last_message_id = max_id
            db.commit()
            logger.info(
                "timing: stm summary %.3fs (session_id=%s, batch=%d, chars=%d, last_msg=%d)",
                time.perf_counter() - t0, session_id, len(batch),
                len(new_summary), max_id,
            )
    except Exception as e:
        logger.warning(f"stm summary failed: {e}")
