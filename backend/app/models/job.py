"""定时任务 ORM 模型（v2 S2：巡检告警 + KB 日报）。

- Job：调度配置（cron/interval + crew + 输入模板 + 推送策略 + 熔断参数）
- JobRun：每次执行的留痕（状态/token 成本/结果快照/推送去向）

安全不变量相关字段（执行计划 §3.2.8）：
- max_consecutive_failures：连续失败 N 次自动停用（默认 5）+ 推一条告警后静默
- pushed_to：'qq' / 'suppressed(eval)' / 'failed(channel_offline)' / null（未推送）
  —— eval 抑制（§5.6）的硬断言锚点，评测 job 用例直接断言该值。
"""
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.crew import CrewConfig

# job_runs.status 取值
RUN_STATUS = ("running", "succeeded", "failed", "disabled_by_circuit", "eval")

# job_runs.pushed_to 取值（None = 本次未尝试推送）
PUSHED_QQ = "qq"
PUSHED_SUPPRESSED_EVAL = "suppressed(eval)"
PUSHED_FAILED_OFFLINE = "failed(channel_offline)"

# job_runs.pushed_to_backup 取值（S3' 钉钉告警备推；None = 未尝试/备推关闭）
PUSHED_BACKUP_DINGTALK = "dingtalk"


class Job(Base, TimestampMixin):
    """一个定时/间隔任务：到点 → 渲染输入模板 → 跑 crew → 按策略推送。"""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # cron | interval（trigger_config 与之匹配：{"expr": "0 8 * * *"} 或 {"seconds": 1800}）
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 巡检走 crew，保持执行链单一（复用 build_crew_from_db 装配）
    crew_id: Mapped[int] = mapped_column(
        ForeignKey("crew_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 支持 {{date}} {{targets_report}} {{kb_delta}} 占位（渲染在 job_runner）
    input_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # {"push": {"on": "state_change|always|daily_summary"}}
    output_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_cap_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    crew: Mapped["CrewConfig"] = relationship(lazy="selectin")

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.name} enabled={self.enabled}>"


class JobRun(Base, TimestampMixin):
    """一次 job 执行的留痕。result_summary 是 state_change 检测的数据源。"""
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # running | succeeded | failed | disabled_by_circuit | eval
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 成本口径说明（token_budget 会话差值法，近似值注明）
    cost_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 巡检存各 target 状态快照（{"targets": {...}, "changes": [...]}）+ events_tail
    result_summary: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    # 'qq' / 'suppressed(eval)' / 'failed(channel_offline)' / 'failed(...)' / null
    pushed_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # S3' 备推落账：'dingtalk' / 'failed(...)' / null（未尝试或备推关闭）——主渠道语义不变
    pushed_to_backup: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<JobRun {self.id} job={self.job_id} status={self.status}>"
