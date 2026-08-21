"""P0-5 聊天限流单测（backend/app/core/rate_limit.py）。

Redis 路径通过 monkeypatch _redis_incr 注入假计数，内存兜底路径真实执行。
"""
import pytest
from fastapi import Request

from app.config import settings
from app.core import rate_limit


class _FakeClient:
    host = "203.0.113.10"


def _make_request(api_key: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/stream",
        "headers": [(b"x-api-key", api_key.encode())] if api_key else [],
        "query_string": b"",
        "client": ("203.0.113.10", 51000),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _isolate():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.mark.asyncio
async def test_within_limit_passes(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 3)
    counter = {"n": 0}

    async def fake_incr(key):
        counter["n"] += 1
        return counter["n"]

    monkeypatch.setattr(rate_limit, "_redis_incr", fake_incr)
    for _ in range(3):
        await rate_limit.check_chat_rate_limit(_make_request())


@pytest.mark.asyncio
async def test_over_limit_raises_429(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 2)
    counter = {"n": 0}

    async def fake_incr(key):
        counter["n"] += 1
        return counter["n"]

    monkeypatch.setattr(rate_limit, "_redis_incr", fake_incr)
    await rate_limit.check_chat_rate_limit(_make_request())
    await rate_limit.check_chat_rate_limit(_make_request())
    with pytest.raises(Exception) as ei:
        await rate_limit.check_chat_rate_limit(_make_request())
    assert getattr(ei.value, "status_code", None) == 429


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_memory(monkeypatch):
    """Redis 抛异常时返回 None，调用方走内存兜底计数，限流仍生效。"""
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 2)

    async def broken_incr(key):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "_redis_incr", broken_incr)
    await rate_limit.check_chat_rate_limit(_make_request())
    await rate_limit.check_chat_rate_limit(_make_request())
    with pytest.raises(Exception) as ei:
        await rate_limit.check_chat_rate_limit(_make_request())
    assert getattr(ei.value, "status_code", None) == 429
    # 内存兜底确实有计数
    assert len(rate_limit._mem_counters) >= 1


@pytest.mark.asyncio
async def test_limit_zero_disables(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 0)
    called = {"n": 0}

    async def fake_incr(key):
        called["n"] += 1
        return 10**9

    monkeypatch.setattr(rate_limit, "_redis_incr", fake_incr)
    for _ in range(50):
        await rate_limit.check_chat_rate_limit(_make_request())
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_client_key_uses_api_key_when_present(monkeypatch):
    """带 X-API-Key 的请求按密钥维度计数（同密钥共享配额）。"""
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 1)
    counter = {"n": 0}

    async def fake_incr(key):
        counter["n"] += 1
        counter["key"] = key
        return counter["n"]

    monkeypatch.setattr(rate_limit, "_redis_incr", fake_incr)
    await rate_limit.check_chat_rate_limit(_make_request(api_key="sk-test-key-123"))
    assert "key:sk-test-key-123" in counter["key"]
