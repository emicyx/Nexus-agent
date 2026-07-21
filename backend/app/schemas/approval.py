"""Approval schemas (Week 5 HITL)"""
from pydantic import BaseModel, Field


class ApprovalDecision(BaseModel):
    """前端提交审批决策。"""
    decision: str = Field(..., description="approve 或 reject")
    comment: str = ""


class ApprovalRead(BaseModel):
    """审批单状态。"""
    approval_id: str
    status: str  # PENDING / APPROVED / REJECTED / TIMEOUT
    action: str
    risk_level: str = "medium"
    reason: str = ""
    agent_role: str = ""
    comment: str = ""
    created_at: float = 0
    resolved_at: float | None = None
    timeout: int = 150
