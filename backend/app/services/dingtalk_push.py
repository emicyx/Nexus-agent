"""钉钉群自定义机器人 webhook 推送（v2 S3'：QQ 告警的备用通道）。

协议事实（2026-09-17，按钉钉官方自定义机器人 demo 实现）：
- 加签：sign = urlencode(base64(HMAC-SHA256(key=secret, msg="{timestamp_ms}\\n{secret}")))，
  以 &timestamp=..&sign=.. 追加到 webhook URL（webhook 已含 access_token 参数）；
  本机时钟与钉钉服务器偏差 >1 小时会拒签（errcode 310000）。
- 消息体 {"msgtype":"text","text":{"content": 段}}，纯文本；响应 errcode==0 判成功。
  不启用钉钉 markdown——与 QQ 渠道同构（格式面最小，转交前缀/分段逻辑零差异）。
- 限流 20 条/分钟（机器人侧硬限）：state_change + 日报推送天然稀疏，不做客户端限速。
- 无状态 HTTP：无"渠道在线"概念，超时/失败单次尝试不重试（QQ 渠道的断连重查
  降级逻辑在此不适用，失败如实落账 failed(...)）。

安全不变量（执行计划 §3.2）：目标（webhook+secret）只来自 env DINGTALK_PUSH_*，
不是模型可填参数（不变量 1）；eval 零外发闸在 egress 上游——eval 早返回时本模块
不会被调用（不变量 2 延伸，集成测试断言两渠道调用数均为 0）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import requests

from app.channels.im_pipeline import split_long_message
from app.channels.onebot_adapter import SendResult
from app.config import settings

logger = logging.getLogger("services.dingtalk_push")

_TIMEOUT_S = 10.0
# 分段间隔（与 send_qq_message 同款温和频控；限流 20 条/分钟下 0.3s 间隔安全）
_SEGMENT_GAP_S = 0.3


def signed_webhook_url(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    """官方 demo 同款加签 URL（纯函数；timestamp 注入以便固定向量单测）。"""
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def send_dingtalk_text(
    content: str,
    *,
    webhook: str,
    secret: str,
    max_len: int | None = None,
) -> SendResult:
    """同步发送纯文本（超长自动分段）。渠道发送细节封闭在此，egress 只做 mode 判定。

    网络异常/errcode 非 0 如实返回 SendResult(ok=False, reason=...)；
    retcode 存失败段的 errcode（成功时 0）。
    """
    segments = split_long_message(content, limit=max_len)
    if not segments:
        return SendResult(ok=True, retcode=0, segments=0)
    sent = 0
    for i, seg in enumerate(segments):
        url = signed_webhook_url(webhook, secret)
        try:
            resp = requests.post(
                url,
                json={"msgtype": "text", "text": {"content": seg}},
                timeout=_TIMEOUT_S,
            )
            data = resp.json()
        except requests.RequestException as e:
            return SendResult(
                ok=False, reason=f"{type(e).__name__}", segments=sent
            )
        errcode = data.get("errcode")
        if errcode != 0:
            return SendResult(
                ok=False,
                retcode=errcode if isinstance(errcode, int) else -1,
                reason=f"errcode={errcode} {str(data.get('errmsg', ''))[:120]}",
                segments=sent,
            )
        sent += 1
        if i < len(segments) - 1:
            time.sleep(_SEGMENT_GAP_S)
    return SendResult(ok=True, retcode=0, segments=sent)


async def send_dingtalk_message(content: str) -> SendResult:
    """异步入口（egress 调用）：凭据来自 env，阻塞 IO 经 to_thread 让出事件循环。"""
    return await asyncio.to_thread(
        send_dingtalk_text,
        content,
        webhook=settings.DINGTALK_PUSH_WEBHOOK,
        secret=settings.DINGTALK_PUSH_SECRET,
    )


def configured() -> bool:
    """备推是否已配置（webhook 与 secret 成对）。未配置时 egress 跳过并记 WARNING。"""
    return bool(settings.DINGTALK_PUSH_WEBHOOK and settings.DINGTALK_PUSH_SECRET)
