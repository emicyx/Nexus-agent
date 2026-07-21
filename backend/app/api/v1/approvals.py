"""HITL 审批路由（Week 5）

前端通过本接口提交审批决策，工具层轮询 Redis 检测状态变更。
"""
import json
import logging

from fastapi import APIRouter, HTTPException

from app.db.redis import approval_key, get_async_redis, update_approval
from app.schemas.approval import ApprovalDecision, ApprovalRead

router = APIRouter()
logger = logging.getLogger("approvals")


@router.get("/pending/list", response_model=list[ApprovalRead])
async def list_pending_approvals():
    """列出所有待审批单（调试用）。"""
    r = get_async_redis()
    keys = await r.keys("approval:*")
    result = []
    for k in keys:
        raw = await r.get(k)
        if raw:
            data = json.loads(raw)
            if data.get("status") == "PENDING":
                result.append(ApprovalRead(**data))
    return result


@router.get("/{approval_id}", response_model=ApprovalRead)
async def get_approval(approval_id: str):
    """查询审批单状态（前端轮询备用，SSE 已推送则不必轮询）。"""
    r = get_async_redis()
    raw = await r.get(approval_key(approval_id))
    if raw is None:
        raise HTTPException(404, "审批单不存在或已过期")
    return ApprovalRead(**json.loads(raw))


@router.post("/{approval_id}", response_model=ApprovalRead)
async def resolve_approval(approval_id: str, payload: ApprovalDecision):
    """提交审批决策（approve/reject）。"""
    decision = payload.decision.lower().strip()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须是 approve 或 reject")

    status = "APPROVED" if decision == "approve" else "REJECTED"
    ok = await update_approval(approval_id, status, payload.comment)
    if not ok:
        raise HTTPException(404, "审批单不存在或已过期")

    r = get_async_redis()
    raw = await r.get(approval_key(approval_id))
    data = json.loads(raw) if raw else {}
    logger.info(f"approval_resolved: id={approval_id} status={status}")
    return ApprovalRead(**data)
