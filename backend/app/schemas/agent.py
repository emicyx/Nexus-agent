"""Agent schemas"""
from pydantic import BaseModel, ConfigDict

from app.schemas.skill import SkillRead
from app.schemas.tool import ToolRead


class AgentBase(BaseModel):
    name: str
    role: str
    goal: str
    backstory: str
    llm_model: str | None = None
    temperature: float | None = None
    max_iter: int = 8
    memory: bool = False


class AgentCreate(AgentBase):
    tool_ids: list[int] = []  # 创建时指定挂载的工具
    skill_ids: list[int] = []  # 创建时指定挂载的 skills


class AgentUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    llm_model: str | None = None
    temperature: float | None = None
    max_iter: int | None = None
    memory: bool | None = None
    tool_ids: list[int] | None = None  # None=不修改，[]清空，[...]替换
    skill_ids: list[int] | None = None  # None=不修改，[]清空，[...]替换


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    goal: str
    backstory: str
    llm_model: str | None
    temperature: float | None
    max_iter: int
    memory: bool
    tools: list[ToolRead] = []
    skills: list[SkillRead] = []
