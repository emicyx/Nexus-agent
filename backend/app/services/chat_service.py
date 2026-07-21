"""Chat session/message 服务层 — DB 持久化对话历史"""
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ChatMessage, ChatSession
from app.schemas.chat import ChatSessionCreate, ChatSessionUpdate


async def list_sessions(
    session: AsyncSession,
    crew_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    """列出 sessions，按 updated_at 倒序。返回含 message_count + last_message_at 聚合。"""
    # 子查询：每个 session 的 message_count
    cnt_subq = (
        select(
            ChatMessage.session_id.label("sid"),
            func.count(ChatMessage.id).label("cnt"),
            func.max(ChatMessage.created_at).label("last_at"),
        )
        .group_by(ChatMessage.session_id)
        .subquery()
    )

    stmt = (
        select(
            ChatSession,
            func.coalesce(cnt_subq.c.cnt, 0).label("message_count"),
            cnt_subq.c.last_at.label("last_message_at"),
        )
        .outerjoin(cnt_subq, ChatSession.id == cnt_subq.c.sid)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    if crew_id is not None:
        stmt = stmt.where(ChatSession.crew_id == crew_id)

    rows = (await session.execute(stmt)).all()
    result = []
    for row in rows:
        s = row[0]
        result.append(
            {
                "id": s.id,
                "crew_id": s.crew_id,
                "session_uuid": s.session_uuid,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": int(row[1] or 0),
                "last_message_at": row[2],
            }
        )
    return result


async def get_session_detail(
    session: AsyncSession,
    session_id: int,
) -> Optional[dict]:
    """获取 session 详情含 messages（selectin 已加载）。"""
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    s = (await session.execute(stmt)).scalar_one_or_none()
    if s is None:
        return None
    return {
        "id": s.id,
        "crew_id": s.crew_id,
        "session_uuid": s.session_uuid,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "message_count": len(s.messages),
        "last_message_at": s.messages[-1].created_at if s.messages else None,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in s.messages
        ],
    }


async def get_session_by_uuid(
    session: AsyncSession,
    session_uuid: str,
) -> Optional[ChatSession]:
    """按 uuid 查 session（chat stream 内部使用）。"""
    stmt = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.session_uuid == session_uuid)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_session(
    session: AsyncSession,
    payload: ChatSessionCreate,
) -> ChatSession:
    """创建新 session。"""
    s = ChatSession(
        crew_id=payload.crew_id,
        session_uuid=payload.session_uuid,
        title=payload.title,
    )
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return s


async def update_session_title(
    session: AsyncSession,
    session_id: int,
    payload: ChatSessionUpdate,
) -> Optional[ChatSession]:
    """更新 session 标题。"""
    s = await session.get(ChatSession, session_id)
    if s is None:
        return None
    s.title = payload.title
    await session.commit()
    await session.refresh(s)
    return s


async def delete_session(session: AsyncSession, session_id: int) -> bool:
    """删除 session（级联删 messages）。"""
    s = await session.get(ChatSession, session_id)
    if s is None:
        return False
    await session.delete(s)
    await session.commit()
    return True


async def append_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    content: str,
) -> ChatMessage:
    """向 session 追加一条消息，并 touch session.updated_at。"""
    m = ChatMessage(session_id=session_id, role=role, content=content)
    session.add(m)
    # 触发 session.updated_at 刷新
    s = await session.get(ChatSession, session_id)
    if s is not None:
        # 用 setattr 强制刷新
        s.title = s.title
    await session.commit()
    await session.refresh(m)
    return m
