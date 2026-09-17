"""渠道注册表（v2 S4 阶段 A）：渠道状态的聚合点。

具体渠道模块在 import 时自注册（register_channel(name, get_status)）；
消费方（GET /v1/channels、/metrics、前端 chip 轮询）只遍历此表，不 import
具体渠道模块——第二入站渠道接入时消费方零改动。

新增渠道：在 _load_builtin 里补一行 import（触发该渠道模块自注册）。
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("channels.registry")

StatusProvider = Callable[[], dict]

_providers: dict[str, StatusProvider] = {}
_builtin_loaded = False


def _load_builtin() -> None:
    """首次访问时加载内置渠道模块（import 副作用：各自完成自注册）。"""
    global _builtin_loaded
    if _builtin_loaded:
        return
    _builtin_loaded = True
    from app.channels import onebot_adapter  # noqa: F401  (S1 QQ 渠道)


def register_channel(name: str, provider: StatusProvider) -> None:
    """渠道模块自注册入口（幂等，同名覆盖）。"""
    _providers[name] = provider


def channel_statuses() -> dict[str, dict]:
    """各渠道在线状态快照；单渠道异常不拖垮整体（返回 error 标记）。"""
    _load_builtin()
    out: dict[str, dict] = {}
    for name, provider in _providers.items():
        try:
            out[name] = provider()
        except Exception:  # noqa: BLE001 - 状态查询异常不拖垮整体
            logger.exception("registry: 渠道 %s 状态查询异常", name)
            out[name] = {"connected": False, "error": True}
    return out
