"""HITL 人类在环审批工具（Week 5）

Agent 调用本工具时，会向 Redis 写入 PENDING 审批单，推送 SSE 事件，
然后同步轮询 Redis 等待前端人类决策（approve/reject）。
超时自动 reject。

关键：_run 是同步方法，CrewAI akickoff() 在主事件循环调用工具，
不能用 asyncio，必须用同步 Redis 客户端 + 同步事件推送。
"""
import asyncio
import logging
import time
import uuid
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.core.events import AgentEvent
from app.db.redis import (
    get_approval_sync,
    set_approval_pending_sync,
    update_approval_sync,
)

logger = logging.getLogger("hitl")

# 默认超时（秒）：前端 120s 超时，工具 60s 兜底（给用户足够但不夸张的决策时间）
DEFAULT_TIMEOUT = 60
POLL_INTERVAL = 1.0


def _safe_put(queue: "asyncio.Queue[AgentEvent | None]", evt: AgentEvent, loop: asyncio.AbstractEventLoop) -> None:
    """线程安全的 Queue 推送。

    Crew 执行在 thread pool 中运行，不在事件循环线程，必须用
    call_soon_threadsafe 投递到事件循环线程 push，避免数据竞争。
    """
    loop.call_soon_threadsafe(queue.put_nowait, evt)


class HumanApprovalInput(BaseModel):
    """审批工具输入。"""
    action: str = Field(
        ...,
        description=(
            "需要人类审批的操作描述，必须具体。"
            "例如：'删除用户表的所有数据'、'发送邮件给客户'、'执行 SQL: DROP TABLE users'。"
            "不能为空。"
        ),
    )
    risk_level: str = Field(
        "medium",
        description="风险等级：low/medium/high/critical。high 以上必须审批。",
    )
    reason: str = Field(
        "",
        description="为什么需要执行此操作的理由（可选）。",
    )


class HumanApprovalTool(BaseTool):
    """
    人类在环审批工具（HITL）。

    当 Agent 决定执行高危操作时，应调用本工具请求人类审批。
    工具会暂停 Agent 执行，等待人类通过前端界面批准或拒绝。
    超时自动拒绝（默认 150 秒）。
    """
    name: str = "human_approval"
    description: str = (
        "请求人类审批高危操作。"
        "触发时机：当你要执行可能造成数据丢失、发送外部消息、"
        "修改重要配置等不可逆操作时，必须先调用本工具获得人类批准。"
        "调用后 Agent 会暂停，等待人类决策。"
    )
    args_schema: type[BaseModel] = HumanApprovalInput

    # 事件推送（由 factory.py 注入）
    event_queue: Any = None  # asyncio.Queue[AgentEvent | None]
    event_loop: Any = None   # asyncio.AbstractEventLoop
    agent_role: str = "Agent"
    timeout: int = DEFAULT_TIMEOUT

    def bind_event_emitter(
        self,
        queue: "asyncio.Queue[AgentEvent | None]",
        loop: asyncio.AbstractEventLoop,
        agent_role: str = "Agent",
    ) -> "HumanApprovalTool":
        """注入事件队列和角色名（factory.py 构造时调用）。"""
        self.event_queue = queue
        self.event_loop = loop
        self.agent_role = agent_role
        return self

    def _run(
        self,
        action: str,
        risk_level: str = "medium",
        reason: str = "",
        **kwargs: Any,
    ) -> str:
        """同步入口：写 Redis PENDING → 推事件 → 轮询 → 返回决策。"""
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        created_at = time.time()

        # 构造审批单
        approval_data = {
            "approval_id": approval_id,
            "status": "PENDING",
            "action": action,
            "risk_level": risk_level,
            "reason": reason,
            "agent_role": self.agent_role,
            "created_at": created_at,
            "timeout": self.timeout,
        }

        # 写入 Redis（同步）
        try:
            set_approval_pending_sync(approval_id, approval_data)
        except Exception as e:
            logger.exception("redis_set_failed")
            return f"审批工具初始化失败（Redis 不可用）: {e}"

        # 推送 approval_requested SSE 事件
        if self.event_queue and self.event_loop:
            _safe_put(
                self.event_queue,
                AgentEvent(
                    type="approval_requested",
                    content=action,
                    agent=self.agent_role,
                    tool="human_approval",
                    input={
                        "approval_id": approval_id,
                        "action": action,
                        "risk_level": risk_level,
                        "reason": reason,
                        "timeout": self.timeout,
                    },
                ),
                self.event_loop,
            )
            logger.info(f"approval_requested: id={approval_id} action={action[:60]}")

        # 同步轮询 Redis
        deadline = created_at + self.timeout
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                state = get_approval_sync(approval_id)
            except Exception as e:
                logger.warning(f"redis_get_failed: {e}")
                continue

            if state is None:
                # Redis key 过期或被删
                return f"审批单已失效或被删除（approval_id={approval_id}）"

            status = state.get("status", "PENDING")
            if status == "APPROVED":
                comment = state.get("comment", "")
                logger.info(f"approval_approved: id={approval_id}")
                return f"已获人类批准。操作：{action}" + (f"。备注：{comment}" if comment else "")
            if status == "REJECTED":
                comment = state.get("comment", "")
                logger.info(f"approval_rejected: id={approval_id}")
                return f"已被人类拒绝。操作：{action}" + (f"。理由：{comment}" if comment else "。请考虑替代方案或放弃此操作。")

        # 超时
        logger.warning(f"approval_timeout: id={approval_id}")
        update_approval_sync(approval_id, "TIMEOUT", "自动超时未响应")
        return f"审批超时（{self.timeout}秒无响应），操作未执行：{action}。请稍后重试或联系管理员。"
