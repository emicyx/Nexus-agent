"""Skill schemas"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class SkillBase(BaseModel):
    name: str
    description: str = ""
    prompt_template: str
    skill_key: str | None = None
    config_json: dict[str, Any] | None = None


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt_template: str | None = None
    skill_key: str | None = None
    config_json: dict[str, Any] | None = None


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    prompt_template: str
    skill_key: str | None
    config_json: dict[str, Any] | None = None
