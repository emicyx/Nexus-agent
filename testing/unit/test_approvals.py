"""HITL 审批状态机单测（backend/app/db/redis.py + tools/human_approval_tool.py）。

用内存 FakeRedis 替换真实 Redis 客户端，验证状态流转与工具轮询行为。
"""
import pytest

import app.db.redis as redis_mod
import app.tools.human_approval_tool as hitl_mod
from app.db.redis import (
    APPROVAL_KEY_PREFIX,
    approval_key,
    get_approval_sync,
    set_approval_pending_sync,
    update_approval_sync,
)
from app.tools.human_approval_tool import HumanApprovalTool


class _Store:
    """共享内存存储：同步/异步两个 client 读写同一份数据。"""

    def __init__(self):
        self._data: dict[str, tuple[str, int | None]] = {}


class FakeRedis:
    """同步 Redis 客户端（CrewAI 工具层）。"""

    def __init__(self, store: _Store):
        self._s = store

    def set(self, key, value, ex=None):
        self._s._data[key] = (value, ex)

    def get(self, key):
        item = self._s._data.get(key)
        return item[0] if item else None

    def delete(self, key):
        self._s._data.pop(key, None)
        return 1


class FakeAsyncRedis:
    """异步 Redis 客户端（FastAPI API 层），await r.get()/r.set()。"""

    def __init__(self, store: _Store):
        self._s = store

    async def set(self, key, value, ex=None):
        self._s._data[key] = (value, ex)

    async def get(self, key):
        item = self._s._data.get(key)
        return item[0] if item else None

    async def delete(self, key):
        self._s._data.pop(key, None)
        return 1


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """把同步/异步 Redis 客户端替换为共享同一内存 store 的 fake。"""
    store = _Store()
    sync_client = FakeRedis(store)
    async_client = FakeAsyncRedis(store)
    monkeypatch.setattr(redis_mod, "get_sync_redis", lambda: sync_client)
    monkeypatch.setattr(redis_mod, "get_async_redis", lambda: async_client)
    return sync_client


# ---------- redis.py 状态机 ----------

def test_approval_key_format():
    assert approval_key("abc123") == f"{APPROVAL_KEY_PREFIX}abc123"


def test_pending_then_approve_sync():
    set_approval_pending_sync("t1", {"approval_id": "t1", "status": "PENDING"})
    state = get_approval_sync("t1")
    assert state["status"] == "PENDING"
    assert update_approval_sync("t1", "APPROVED", "允许")
    state = get_approval_sync("t1")
    assert state["status"] == "APPROVED"
    assert state["comment"] == "允许"
    assert state["resolved_at"] is not None


def test_update_nonexistent_returns_false():
    assert update_approval_sync("missing", "APPROVED") is False


async def test_async_update_approval():
    set_approval_pending_sync("t2", {"approval_id": "t2", "status": "PENDING"})
    ok = await redis_mod.update_approval("t2", "REJECTED", "不行")
    assert ok is True
    state = get_approval_sync("t2")
    assert state["status"] == "REJECTED"


# ---------- HumanApprovalTool 轮询 ----------

def _make_tool(timeout=3):
    tool = HumanApprovalTool()
    tool.timeout = timeout
    return tool


def test_tool_approved(monkeypatch):
    monkeypatch.setattr(hitl_mod.time, "sleep", lambda s: None)
    state = {"status": "APPROVED", "comment": "ok"}
    monkeypatch.setattr(hitl_mod, "get_approval_sync", lambda aid: state)
    result = _make_tool()._run("删除用户表数据", "critical")
    assert "已获人类批准" in result
    assert "删除用户表数据" in result


def test_tool_rejected(monkeypatch):
    monkeypatch.setattr(hitl_mod.time, "sleep", lambda s: None)
    state = {"status": "REJECTED", "comment": "不要删"}
    monkeypatch.setattr(hitl_mod, "get_approval_sync", lambda aid: state)
    result = _make_tool()._run("删除用户表数据", "critical")
    assert "已被人类拒绝" in result
    assert "不要删" in result


def test_tool_timeout_sets_timout(monkeypatch):
    monkeypatch.setattr(hitl_mod.time, "sleep", lambda s: None)
    # 永远 PENDING → 轮询到超时
    monkeypatch.setattr(hitl_mod, "get_approval_sync", lambda aid: {"status": "PENDING"})
    recorded = {}
    monkeypatch.setattr(hitl_mod, "update_approval_sync", lambda aid, st, comment="": recorded.setdefault("status", st) or True)
    result = _make_tool(timeout=2)._run("危险操作", "high")
    assert "审批超时" in result
    assert recorded.get("status") == "TIMEOUT"


def test_tool_redis_init_failure(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(hitl_mod, "set_approval_pending_sync", boom)
    result = _make_tool()._run("x", "medium")
    assert "审批工具初始化失败" in result
