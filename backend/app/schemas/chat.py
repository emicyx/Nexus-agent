"""Chat session/message Pydantic schemas"""
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class ChatMessageRead(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionBase(BaseModel):
    crew_id: int
    session_uuid: str = Field(..., min_length=8, max_length=64)
    title: str = Field(default="新对话", max_length=200)


class ChatSessionCreate(ChatSessionBase):
    """创建 session：前端生成 uuid 传入"""


class ChatSessionUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ChatSessionRead(BaseModel):
    id: int
    crew_id: int
    session_uuid: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message_at: datetime | None = None

    model_config = {"from_attributes": True}


class ChatSessionDetail(ChatSessionRead):
    """session 详情含完整 messages"""
    messages: list[ChatMessageRead] = []
