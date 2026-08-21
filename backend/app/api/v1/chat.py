"""SSE 流式聊天接口"""
import asyncio
import contextlib
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.core.events import AgentEvent, event_stream, try_put
from app.core.llm_errors import classify_llm_error
from app.core.rate_limit import check_chat_rate_limit
from app.core import run_control
from app.core.run_control import (
    RunCancelledError,
    bind_run_cancel_event,
    cancel_current_run,
    current_cancel_event,
    is_shutting_down,
    new_run_id,
    register_run,
    unregister_run,
)
from app.core.token_budget import (
    BudgetExceededRunError,
    bind_token_session,
    ensure_budget_available,
    unbind_token_session,
)
from app.crews.factory import get_default_crew_id, run_crew_chat, run_single_agent_chat
from app.db.session import AsyncSessionLocal
from app.schemas.chat import ChatSessionCreate
from app.services import chat_service

router = APIRouter()
logger = logging.getLogger("chat")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # 前端生成的 uuid
    # Week 3: crew_id 指定 DB 中的 Crew 配置；None=用默认种子 crew
    crew_id: int | None = None
    # single=true 时走无 DB 的单 Agent 回退
    single: bool = False


async def _ensure_session(
    session_uuid: str,
    crew_id: int,
    first_message: str,
) -> int | None:
    """确保 DB 中存在该 session_uuid 的 ChatSession；不存在则创建（title=首条消息前 30 字）。

    返回 session_id (int)，失败返回 None。
    """
    async with AsyncSessionLocal() as db:
        sess = await chat_service.get_session_by_uuid(db, session_uuid)
        if sess is not None:
            return sess.id
        # 创建新 session
        title = (first_message.strip()[:30] or "新对话")
        payload = ChatSessionCreate(
            crew_id=crew_id,
            session_uuid=session_uuid,
            title=title,
        )
        new_sess = await chat_service.create_session(db, payload)
        return new_sess.id


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """接收用户消息，启动 Agent 执行，返回 SSE 流。"""
    # B4 优雅停机：停机窗口内拒绝新会话（进行中的可收尾）
    if is_shutting_down():
        raise HTTPException(status_code=503, detail="服务正在关闭，暂不接受新会话，请稍后重试")

    # P0-5 限流：每分钟固定窗口（调用方维度）+ 并发 SSE 运行上限。
    # 每个请求一个 Crew + worker 线程 + SSE 队列，无限流时并发打满即成本 DoS
    await check_chat_rate_limit(request)
    if (
        settings.MAX_CONCURRENT_RUNS > 0
        and run_control.active_run_count() >= settings.MAX_CONCURRENT_RUNS
    ):
        raise HTTPException(
            status_code=503,
            detail=f"服务繁忙：并发会话已达上限（{settings.MAX_CONCURRENT_RUNS}），请稍后重试。",
        )

    # A3 有界队列：慢客户端积累事件时丢弃最旧，防内存无界增长
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=settings.SSE_QUEUE_MAXSIZE)
    loop = asyncio.get_running_loop()

    # 解析 crew_id：single 模式优先，否则用传入 crew_id 或默认 crew
    crew_id = req.crew_id
    if crew_id is None and not req.single:
        crew_id = await get_default_crew_id()

    # Week 11+: 确保 DB session 存在（首次发消息时 lazy 创建）
    db_session_id: int | None = None
    if req.session_id and crew_id is not None and not req.single:
        try:
            db_session_id = await _ensure_session(req.session_id, crew_id, req.message)
        except Exception:
            logger.exception("ensure_session failed (非致命)")

    run_id = new_run_id()

    async def producer():
        # A2 会话归集：本请求的 LLM 用量计入该会话（contextvar 随 to_thread 复制）
        session_token = bind_token_session(req.session_id)
        try:
            # A2 日预算熔断：新请求开始前检查（超限走下方错误分类 → budget_exceeded）
            await ensure_budget_available()
            if req.single or crew_id is None:
                # 无 DB 配置时回退到单 Agent
                await run_single_agent_chat(req.message, queue, loop)
            else:
                await run_crew_chat(crew_id, req.message, queue, loop, session_id=req.session_id)
        except RunCancelledError:
            # A1：客户端已断连，事件无处投递，静默收尾即可
            logger.info("run_cancelled: run_id=%s session=%s", run_id, req.session_id)
        except BudgetExceededRunError as e:
            # A2 中途预算熔断：worker 线程检查点抛出的 BaseException 载体，
            # 转发原始 TokenBudgetExceededError 走分类管道（budget_exceeded）
            logger.warning("run_budget_exceeded: run_id=%s session=%s", run_id, req.session_id)
            kind, user_message = classify_llm_error(e.original)
            await queue.put(
                AgentEvent(type="error", content=user_message, error_kind=kind)
            )
        except Exception as e:
            logger.exception("crew_execution_failed")
            # 熔断报错提示方案：分类 + 用户友好文案（见 core/llm_errors.py）
            kind, user_message = classify_llm_error(e)
            await queue.put(
                AgentEvent(type="error", content=user_message, error_kind=kind)
            )
        finally:
            unbind_token_session(session_token)
            # 哨兵，通知 event_stream 结束。消费端已断开且队列满时 put 会永久阻塞，
            # 超时后丢最旧强塞（此时哨兵已无观察者，只为 producer 不悬挂）
            try:
                await asyncio.wait_for(queue.put(None), timeout=5.0)
            except asyncio.TimeoutError:
                try_put(queue, None)

    # A1 取消标志：先绑定（producer 及其 worker 线程经 contextvar 继承同一 Event），
    # 再创建 producer 任务。不持 Token 复位：请求上下文随任务结束自然消亡
    bind_run_cancel_event()
    producer_task = asyncio.create_task(producer())
    # B4/B3：登记到活跃运行注册表（优雅停机收尾 + /metrics 并发与队列深度）
    register_run(
        run_id,
        session=req.session_id or run_id,
        event=current_cancel_event(),
        task=producer_task,
        queue=queue,
    )

    async def stream_generator():
        try:
            async for chunk in event_stream(queue):
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            # A1：断连（或流正常结束）→ set 取消标志（worker 线程内的检查点
            # 由此终止：工具调用前 / HITL 轮询 / 委派子 Agent 前）+ cancel
            # producer（中断事件循环上的 LLM await），并回收任务避免
            # "exception was never retrieved" 告警。
            # 注意：生成器的 finally 可能在与绑定不同的 Context 中执行
            # （响应流式在 TaskGroup 任务里迭代），不能用 Token.reset——
            # 请求上下文随任务结束自然消亡，不会跨请求泄漏。
            cancel_current_run()
            if not producer_task.done():
                producer_task.cancel()
            with contextlib.suppress(BaseException):
                await producer_task
            unregister_run(run_id)

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
