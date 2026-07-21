"""OutputSchema schemas"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class OutputSchemaBase(BaseModel):
    name: str
    description: str = ""
    schema_fields: list[dict[str, Any]] = []


class OutputSchemaCreate(OutputSchemaBase):
    pass


class OutputSchemaUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    schema_fields: list[dict[str, Any]] | None = None


class OutputSchemaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    schema_fields: list[dict[str, Any]]
