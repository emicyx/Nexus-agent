"""OneBot 11 反向 WebSocket adapter（v2 S1：QQ 双向问答的渠道底座）。

拓扑：NapCat 容器（compose 第 5 服务）反向连接本服务的
`/v1/channels/onebot/ws?token=...`（compose 内网明文）——adapter 与部署
位置无关（本机/云端同一代码路径，只是 URL 不同）。

职责边界（S4 阶段 A 起，渠道差异封闭清单）：
- 传输层：WS 连接/鉴权/顶替/接收循环（token 校验失败立即断开，安全不变量 5）；
- 原生事件解析：群 @ 判定与剥离、群/私聊判定、字段容错；
- owner env 解析（QQ_OWNER_IDS，安全不变量 6）；
- 回复发送 API：send_qq_message() 是全系统唯一 QQ 发送出口（S2 推送复用），
  对话回复（reply-to-source 闭包）与主动推送是两条路径、同一个出口函数；
- 在线状态：get_status()（注册表聚合 + /metrics gauge 数据源）。

入站语义（白名单/路由/会话/串行/执行/回复文案）全部来自渠道无关的
im_pipeline：本模块只把原生事件解析成 InboundMessage 交给 handle_inbound。
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import re
import time
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.channels import im_pipeline, registry
from app.channels.im_pipeline import InboundMessage, split_long_message
from app.config import settings

logger = logging.getLogger("channels.onebot")

router = APIRouter()

# CQ 码 at 段（raw_message 回退解析用）
_CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)[^\]]*\]\s*")


# ─────────────────────────── 纯函数（单测覆盖） ───────────────────────────

def extract_at_and_text(message: dict | list | str, raw_message: str, self_id: str) -> tuple[bool, str]:
    """群消息解析：是否 @ 了本机器人，以及剥离 at 后的正文。

    message 字段优先（数组段格式，NapCat 结构化可靠），raw_message 的
    CQ 码作回退。私聊消息不经此函数。
    """
    self_id = str(self_id).strip()
    if not self_id:
        return False, ""
    if isinstance(message, list):
        hit = False
        texts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "at" and str(seg.get("data", {}).get("qq", "")) == self_id:
                hit = True
            elif seg.get("type") == "text":
                texts.append(str(seg.get("data", {}).get("text", "")))
        return hit, "".join(texts).strip()
    # raw_message CQ 码回退：剥掉全部 at 段后若命中过 self_id 则为 @ 本机
    hit = False
    text = raw_message or ""
    for m in _CQ_AT_RE.finditer(text):
        if m.group(1) == self_id:
            hit = True
    return hit, _CQ_AT_RE.sub("", text).strip()


def owner_ids() -> set[str]:
    """解析 QQ_OWNER_IDS（逗号分隔）。空配置 = 名单为空（一切入站都拒）。

    经 int() 数字化归一（"010001" 与 10001 等价），与事件侧 sender 解析同构。
    """
    return {str(int(x)) for x in settings.QQ_OWNER_IDS.replace("，", ",").split(",") if x.strip()}


# ─────────────────────────── 出站：唯一 QQ 发送出口 ───────────────────────────

@dataclass
class SendResult:
    ok: bool
    retcode: int | None = None
    reason: str = ""  # channel_offline / timeout / api_error ...
    segments: int = 0


class _ConnState:
    """单连接模型状态：当前活跃 WS + echo 应答表。"""

    def __init__(self) -> None:
        self.ws: WebSocket | None = None
        self.connected_since: float | None = None
        self._echo_futures: dict[str, asyncio.Future] = {}
        self._echo_seq = itertools.count(1)

    def is_online(self) -> bool:
        return self.ws is not None

    async def send_api(self, action: str, params: dict, timeout: float = 10.0) -> dict:
        """下发 OneBot API 调用并等待同连接响应（echo 匹配）。"""
        ws = self.ws
        if ws is None:
            raise ConnectionError("onebot channel offline")
        echo = f"nexus-{next(self._echo_seq)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._echo_futures[echo] = fut
        try:
            await ws.send_json({"action": action, "params": params, "echo": echo})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._echo_futures.pop(echo, None)

    def resolve_echo(self, msg: dict) -> None:
        fut = self._echo_futures.get(str(msg.get("echo", "")))
        if fut is not None and not fut.done():
            fut.set_result(msg)


_conn = _ConnState()


def get_status() -> dict:
    """渠道在线状态（渠道注册表与 /metrics onebot_connected 用）。

    configured=ONEBOT_WS_TOKEN 是否已配置——未配置时前端不渲染 chip（避免
    对未启用 QQ 渠道的部署产生常驻噪音）。
    """
    return {
        "connected": _conn.is_online(),
        "connected_since": _conn.connected_since,
        "configured": bool(settings.ONEBOT_WS_TOKEN),
        "label": "QQ",
    }


# 渠道注册表自注册（S4 阶段 A）：消费方遍历 registry，不 import 本模块
registry.register_channel("onebot", get_status)


async def send_qq_message(
    content: str, *, user_id: int | None = None, group_id: int | None = None
) -> SendResult:
    """发送纯文本 QQ 消息（超长自动分段）。全系统唯一 QQ 出口（S2 推送复用）。

    对话回复（reply-to-source）与主动推送（QQ_ALERT_TARGET）都经此函数；
    S1 的回复目标来自事件来源，不引入新的目标面（安全不变量 1）。
    """
    segments = split_long_message(content, limit=settings.QQ_MESSAGE_MAX_LEN)
    if not segments:
        return SendResult(ok=True, segments=0)
    action = "send_private_msg" if user_id is not None else "send_group_msg"
    sent = 0
    try:
        for i, seg in enumerate(segments):
            params: dict = {"message": [{"type": "text", "data": {"text": seg}}]}
            if user_id is not None:
                params["user_id"] = user_id
            else:
                params["group_id"] = group_id
            resp = await _conn.send_api(action, params)
            retcode = int(resp.get("retcode", -1))
            if retcode != 0:
                return SendResult(ok=False, retcode=retcode,
                                  reason=f"api_error(retcode={retcode})", segments=sent)
            sent += 1
            if i < len(segments) - 1:
                await asyncio.sleep(0.3)  # 温和频控：分段间隔
    except asyncio.TimeoutError:
        return SendResult(ok=False, reason="timeout", segments=sent)
    except (ConnectionError, RuntimeError, WebSocketDisconnect) as e:
        return SendResult(ok=False, reason=f"channel_offline({type(e).__name__})", segments=sent)
    return SendResult(ok=True, segments=sent)


# ─────────────────────────── 入站：解析 + 交给管线 ───────────────────────────

async def _reply_to_source(ev: dict, text: str) -> None:
    """回复闭包的实体：回事件来源（reply-to-source），发送细节封闭在本模块。"""
    user_id = ev.get("user_id")
    group_id = ev.get("group_id")
    if group_id is not None:
        result = await send_qq_message(text, group_id=int(group_id))
    elif user_id is not None:
        result = await send_qq_message(text, user_id=int(user_id))
    else:
        logger.warning("onebot: message 事件缺少 user_id/group_id，无法回复")
        return
    if not result.ok:
        logger.warning("onebot: 回复失败 reason=%s segments=%s/%s",
                       result.reason, result.segments,
                       len(split_long_message(text, limit=settings.QQ_MESSAGE_MAX_LEN)))


async def handle_message_event(ev: dict) -> None:
    """入站 message 事件：原生解析 → InboundMessage → 渠道无关管线。"""
    message_type = ev.get("message_type")
    sender_id = str(int((ev.get("sender") or {}).get("user_id") or ev.get("user_id") or 0))

    # 群/私聊判定（原生事件解析，渠道差异封闭点）
    text: str
    is_group = message_type == "group"
    if is_group:
        hit, text = extract_at_and_text(
            ev.get("message"), ev.get("raw_message") or "", settings.QQ_BOT_SELF_ID
        )
        if not hit:
            return  # 未 @ 本机器人：静默（不做会话化，§4.7）
    elif message_type == "private":
        text = str(ev.get("raw_message") or "").strip()
    else:
        return

    if not text:
        return

    msg = InboundMessage(
        channel="qq",
        sender_id=sender_id,
        text=text,
        is_group=is_group,
        owners=owner_ids(),
        reply=lambda reply_text: _reply_to_source(ev, reply_text),
    )
    await im_pipeline.handle_inbound(msg)


# ─────────────────────────── WS endpoint ───────────────────────────

def _log_task_exception(task: "asyncio.Task") -> None:
    """message 处理任务的兜底日志（异常无人 await 时不会静默丢失）。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("onebot: message 处理异常: %s", exc, exc_info=exc)


@router.websocket("/v1/channels/onebot/ws")
async def onebot_ws(ws: WebSocket) -> None:
    """NapCat 反向 WS 接入点。token 校验失败立即关闭（安全不变量 5）。

    单连接模型：新连接顶旧连接（旧连接优雅关闭）——NapCat 断线重连时
    旧连接可能还挂着 TCP 半开，顶替保证 _conn 永远指向最新活跃连接。
    """
    token = ws.query_params.get("token", "")
    expected = settings.ONEBOT_WS_TOKEN
    if not expected or token != expected:
        logger.error("onebot: WS 连接鉴权失败（token 缺失或不匹配），立即关闭")
        await ws.close(code=4401)
        return

    await ws.accept()

    # 顶替旧连接
    old = _conn.ws
    if old is not None:
        logger.info("onebot: 新连接顶替旧连接")
        _conn.ws = None
        with contextlib.suppress(Exception):
            await old.close(code=4000, reason="replaced by new connection")

    _conn.ws = ws
    _conn.connected_since = time.time()
    logger.info("onebot: 渠道已连接")

    try:
        while True:
            msg = await ws.receive_json()
            # API 响应（echo 匹配）
            if "echo" in msg and ("retcode" in msg or "status" in msg):
                _conn.resolve_echo(msg)
                continue
            post_type = msg.get("post_type")
            if post_type == "meta_event":
                logger.debug("onebot: meta_event %s", msg.get("meta_event_type"))
                continue
            if post_type == "message":
                # 管线异步执行，不阻塞接收循环（后续消息/心跳/响应照常收）；
                # 异常必须落日志——裸 create_task 的异常无人 await 会静默丢失
                task = asyncio.create_task(handle_message_event(msg))
                task.add_done_callback(_log_task_exception)
                continue
            logger.debug("onebot: 忽略事件 post_type=%s", post_type)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("onebot: 接收循环异常")
    finally:
        if _conn.ws is ws:
            _conn.ws = None
            _conn.connected_since = None
            logger.info("onebot: 渠道断连（等待 NapCat 自动重连）")
        with contextlib.suppress(Exception):
            await ws.close()
