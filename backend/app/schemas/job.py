"""Job schemas（v2 S2）"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.crew import CrewRead


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    trigger_type: str  # cron | interval（service 层校验）
    trigger_config: dict
    crew_id: int
    input_template: str = ""
    output_config: dict = {}
    cost_cap_tokens: int | None = None
    max_consecutive_failures: int = 5
    enabled: bool = True


class JobUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    trigger_type: str | None = None  # 与 trigger_config 成对提供
    trigger_config: dict | None = None
    crew_id: int | None = None
    input_template: str | None = None
    output_config: dict | None = None
    cost_cap_tokens: int | None = None
    max_consecutive_failures: int | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    enabled: bool
    trigger_type: str
    trigger_config: dict
    crew_id: int
    crew: CrewRead | None = None
    input_template: str
    output_config: dict
    cost_cap_tokens: int | None = None
    max_consecutive_failures: int
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    consecutive_failures: int


class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    status: str
    error: str | None = None
    tokens_used: int
    cost_note: str
    result_summary: dict | None = None
    pushed_to: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class JobTriggerRequest(BaseModel):
    eval: bool = False  # true = eval 模式（零外发，状态记 eval）


class JobTriggerResponse(BaseModel):
    run_id: int
    status: str
    pushed_to: str | None
