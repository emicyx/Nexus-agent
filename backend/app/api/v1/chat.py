"""SSE 流式聊天接口"""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import AgentEvent, event_stream
from app.crews.factory import get_default_crew_id, run_crew_chat, run_single_agent_chat
from app.db.session import AsyncSessionLocal, get_db
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
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
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
        except Exception as e:
            logger.warning(f"ensure_session failed (非致命): {e}")

    async def producer():
        try:
            if req.single or crew_id is None:
                # 无 DB 配置时回退到单 Agent
                await run_single_agent_chat(req.message, queue, loop)
            else:
                await run_crew_chat(crew_id, req.message, queue, loop, session_id=req.session_id)
        except Exception as e:
            logger.exception("crew_execution_failed")
            await queue.put(AgentEvent(type="error", content=str(e)))
        finally:
            await queue.put(None)  # 哨兵，通知 event_stream 结束

    # 启动后台 producer 任务，与 SSE 流并行
    producer_task = asyncio.create_task(producer())

    async def stream_generator():
        try:
            async for chunk in event_stream(queue):
                if await request.is_disconnected():
                    producer_task.cancel()
                    break
                yield chunk
        finally:
            if not producer_task.done():
                producer_task.cancel()

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
