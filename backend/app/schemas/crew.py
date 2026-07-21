"""Crew schemas"""
from pydantic import BaseModel, ConfigDict

from app.schemas.agent import AgentRead
from app.schemas.task import TaskCreate, TaskRead


class CrewBase(BaseModel):
    name: str
    description: str = ""
    process_type: str = "sequential"
    manager_agent_id: int | None = None


class CrewCreate(CrewBase):
    agent_ids: list[int] = []  # 有序，position=索引
    tasks: list[TaskCreate] = []


class CrewUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    process_type: str | None = None
    manager_agent_id: int | None = None  # None=不修改；显式传 null 清除
    agent_ids: list[int] | None = None  # None=不修改
    # tasks 通过子资源接口管理，此处不整体替换


class CrewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    process_type: str
    manager_agent_id: int | None = None
    manager_agent: AgentRead | None = None
    agents: list[AgentRead] = []
    tasks: list[TaskRead] = []
