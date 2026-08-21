"""Request-ID 中间件单测（backend/app/core/middleware.py，上线欠账 B1）。"""
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import (
    RequestIDMiddleware,
    get_request_id,
    install_request_id_logging,
)

app = FastAPI()
app.add_middleware(RequestIDMiddleware)


@app.get("/whoami")
def whoami():
    # 在请求上下文内打一条日志，验证 request_id 被注入
    logging.getLogger("test.middleware").info("inside request")
    return {"rid": get_request_id()}


client = TestClient(app)


def test_generates_request_id_and_echoes_header():
    r = client.get("/whoami")
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) >= 8
    # 请求处理上下文内读取到的是同一个 ID
    assert r.json() == {"rid": rid}


def test_upstream_request_id_passthrough():
    r = client.get("/whoami", headers={"X-Request-ID": "gw-trace-42"})
    assert r.headers.get("x-request-id") == "gw-trace-42"
    assert r.json() == {"rid": "gw-trace-42"}


def test_context_reset_after_request():
    client.get("/whoami")
    # 请求结束后上下文复位，后台日志显示 "-" 而非上一个请求的 ID
    assert get_request_id() == "-"


def test_request_id_injected_into_log_records():
    install_request_id_logging()

    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Capture(level=logging.INFO)
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        # 通过中间件发起请求，期间在请求上下文里打日志
        r = client.get("/whoami")
        rid = r.headers["x-request-id"]
        matching = [rec for rec in captured if getattr(rec, "request_id", None) == rid]
        assert matching, "请求期间产生的日志应携带 request_id"
        assert any(rec.getMessage() == "inside request" for rec in matching)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def test_install_idempotent():
    install_request_id_logging()
    install_request_id_logging()  # 二次安装不叠加


def test_request_id_control_chars_stripped():
    """日志注入防护：客户端头中的换行/控制字符不得进入 request_id（可伪造日志行）。"""
    r = client.get("/whoami", headers={"X-Request-ID": "ab\x00cd\r\nef"})
    rid = r.headers.get("x-request-id")
    assert rid == "abcdef"
    assert r.json() == {"rid": "abcdef"}


def test_request_id_blank_after_sanitize_falls_back_to_generated():
    r = client.get("/whoami", headers={"X-Request-ID": "\r\n\x00"})
    rid = r.headers.get("x-request-id")
    assert rid and "\r" not in rid and "\n" not in rid
    assert r.json() == {"rid": rid}
