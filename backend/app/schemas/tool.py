"""Tool schemas"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolBase(BaseModel):
    name: str
    tool_key: str
    description: str = ""
    config_json: dict[str, Any] | None = None


class ToolCreate(ToolBase):
    pass


class ToolUpdate(BaseModel):
    name: str | None = None
    tool_key: str | None = None
    description: str | None = None
    config_json: dict[str, Any] | None = None


class ToolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tool_key: str
    description: str
    config_json: dict[str, Any] | None = None
