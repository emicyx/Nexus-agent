"""渠道在线状态 API（v2 S1：GET /v1/channels；S4 阶段 A 起遍历渠道注册表）。"""
from fastapi import APIRouter

from app.channels import registry

router = APIRouter()


@router.get("")
async def channel_status():
    """渠道在线状态（前端 /chat 顶栏 chip 轮询；推送前 job 侧也会查）。"""
    return registry.channel_statuses()
