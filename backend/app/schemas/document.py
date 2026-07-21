"""Document schemas（Week 4 RAG）"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DocumentCreate(BaseModel):
    name: str
    content: str
    source_type: str = "text"


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: str
    chunk_count: int = 0
    created_at: datetime


class SearchResult(BaseModel):
    content: str
    document_name: str
    position: int
    score: float
