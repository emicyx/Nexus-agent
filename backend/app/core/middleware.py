"""Request-ID 中间件与日志关联（上线欠账 B1）。

背景：全库已有大量 timing: 埋点，但没有关联 ID，一次请求散落的几十条日志
无法串成全链路——排障时只能靠时间戳猜。

方案：
- 纯 ASGI 中间件（不走 BaseHTTPMiddleware，避免其已知问题影响 SSE 流式
  与 request.is_disconnected() 断连检测）：每个 HTTP 请求生成短 ID
  （或透传上游 X-Request-ID，供网关/前端串联），写入响应头，并绑定到
  contextvar。
- 经 record factory 把 request_id 注入**所有**日志记录（包括 worker 线程
  ——asyncio.to_thread 复制 contextvars，Crew 执行链内的日志同带此 ID）。
"""
from __future__ import annotations

import contextvars
import logging
import re
import uuid
from typing import Any, Awaitable, Callable

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# 客户端可控头进日志前的消毒：去掉换行/控制字符（防伪造日志行），限长 64
_REQUEST_ID_SANITIZE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def get_request_id() -> str:
    return _request_id_ctx.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class RequestIDMiddleware:
    """纯 ASGI 中间件：注入 X-Request-ID（请求头优先，否则生成）。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict,
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = None
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                # 消毒：客户端可注入 \r\n 伪造日志行（日志 format 直接拼接此值）
                request_id = (
                    _REQUEST_ID_SANITIZE_RE.sub("", value.decode("latin-1"))[:64].strip()
                    or None
                )
                break
        if not request_id:
            request_id = new_request_id()

        token = _request_id_ctx.set(request_id)

        async def send_with_request_id(message: dict) -> Awaitable[None]:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _request_id_ctx.reset(token)


def install_request_id_logging() -> None:
    """把 request_id 注入每条日志记录（record factory 全局生效，含自建 handler 的模块）。"""
    import logging as _logging

    factory = _logging.getLogRecordFactory()

    if getattr(factory, "_has_request_id", False):
        return

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        record.request_id = get_request_id()
        return record

    record_factory._has_request_id = True  # type: ignore[attr-defined]
    _logging.setLogRecordFactory(record_factory)
