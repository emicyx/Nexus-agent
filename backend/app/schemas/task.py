"""Task schemas"""
from pydantic import BaseModel, ConfigDict


class TaskBase(BaseModel):
    name: str
    description: str
    expected_output: str = ""
    agent_id: int | None = None  # None=hierarchical 模式由 manager 动态分配
    position: int = 0
    context_task_ids: list[int] | None = None
    output_schema_id: int | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    expected_output: str | None = None
    agent_id: int | None = None
    position: int | None = None
    context_task_ids: list[int] | None = None
    output_schema_id: int | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crew_id: int
    agent_id: int | None
    name: str
    description: str
    expected_output: str
    position: int
    context_task_ids: list[int] | None = None
    output_schema_id: int | None = None
