"""S3' 钉钉告警备用通道单测：加签 / webhook 发送 / egress 备推编排。

安全不变量锚点（执行计划 §6.3/§6.4）：
- 不变量 1 延伸：备推凭据只来自 env（模块无目标参数面）
- 不变量 2 延伸：eval 模式下 QQ 与钉钉两个出口调用数都必须为 0（硬红线）
- 落账：outcome.backup（'dingtalk' / 'failed(...)' / ''）与 job_runs.pushed_to_backup 对应
"""
from __future__ import annotations

import pytest

from app.channels import onebot_adapter as ob
from app.channels.onebot_adapter import SendResult
from app.config import settings
from app.services import dingtalk_push, egress

# 独立预计算的加签固定向量（2026-09-17，算法与实现解耦——向量先算好 pin 进来）
_VEC_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
_VEC_SECRET = "SEC_test_vector_0123456789"
_VEC_TS = 1700000000000
_VEC_SIGN = "7BPHTubnmm2y%2FNR9%2FadKHTSWnYsqVe3r5dEKc4xOXAQ%3D"


# ── 加签（官方 demo 同款） ────────────────────────────────────────

def test_signed_webhook_url_pinned_vector():
    url = dingtalk_push.signed_webhook_url(_VEC_WEBHOOK, _VEC_SECRET, timestamp_ms=_VEC_TS)
    assert url == (
        f"{_VEC_WEBHOOK}&timestamp={_VEC_TS}&sign={_VEC_SIGN}"
    )


def test_signed_webhook_url_without_query_param():
    """webhook 无 query 参数时用 ? 拼接（防 access_token 缺失形态拼出非法 URL）。"""
    url = dingtalk_push.signed_webhook_url(
        "https://example.com/hook", _VEC_SECRET, timestamp_ms=_VEC_TS
    )
    assert url.startswith("https://example.com/hook?timestamp=")
    assert url.endswith(f"&sign={_VEC_SIGN}")


# ── 发送客户端（mock requests） ──────────────────────────────────

class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def _install_post(monkeypatch, payloads_or_exc: list):
    """按序返回假响应/异常，记录 (url, body) 调用。"""
    calls: list[tuple[str, dict]] = []
    seq = list(payloads_or_exc)

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)

    monkeypatch.setattr(dingtalk_push.requests, "post", fake_post)
    monkeypatch.setattr(dingtalk_push.time, "sleep", lambda *_: None)  # 分段间隔清零
    return calls


def test_send_ok_single_segment(monkeypatch):
    calls = _install_post(monkeypatch, [{"errcode": 0, "errmsg": "ok"}])
    r = dingtalk_push.send_dingtalk_text("hello", webhook=_VEC_WEBHOOK, secret=_VEC_SECRET)
    assert r.ok and r.segments == 1
    assert len(calls) == 1
    url, body = calls[0]
    assert "&timestamp=" in url and "&sign=" in url  # 加签参数在 URL 上
    assert body == {"msgtype": "text", "text": {"content": "hello"}}


def test_send_long_content_splits(monkeypatch):
    calls = _install_post(monkeypatch, [{"errcode": 0}] * 3)
    r = dingtalk_push.send_dingtalk_text("a" * 25, webhook=_VEC_WEBHOOK, secret=_VEC_SECRET, max_len=10)
    assert r.ok and r.segments == 3
    assert [len(c[1]["text"]["content"]) for c in calls] == [10, 10, 5]


def test_send_errcode_failure_recorded(monkeypatch):
    _install_post(monkeypatch, [{"errcode": 310000, "errmsg": "sign not match"}])
    r = dingtalk_push.send_dingtalk_text("x", webhook=_VEC_WEBHOOK, secret=_VEC_SECRET)
    assert not r.ok
    assert "errcode=310000" in r.reason
    assert r.retcode == 310000


def test_send_network_error_recorded(monkeypatch):
    import requests as _requests

    _install_post(monkeypatch, [_requests.ConnectionError("boom")])
    r = dingtalk_push.send_dingtalk_text("x", webhook=_VEC_WEBHOOK, secret=_VEC_SECRET)
    assert not r.ok and r.reason == "ConnectionError"


def test_send_empty_content_no_call(monkeypatch):
    calls = _install_post(monkeypatch, [])
    r = dingtalk_push.send_dingtalk_text("   ", webhook=_VEC_WEBHOOK, secret=_VEC_SECRET)
    assert r.ok and r.segments == 0 and calls == []


def test_configured_flag(monkeypatch):
    monkeypatch.setattr(settings, "DINGTALK_PUSH_WEBHOOK", "")
    monkeypatch.setattr(settings, "DINGTALK_PUSH_SECRET", "s")
    assert not dingtalk_push.configured()
    monkeypatch.setattr(settings, "DINGTALK_PUSH_WEBHOOK", "https://x/hook?access_token=1")
    assert dingtalk_push.configured()


# ── egress 备推编排（mode 三态 + eval 双抑制） ────────────────────

def _install_channels(monkeypatch, *, qq_online: bool, backup_ret=None):
    """mock 主通道（onebot）与备推（dingtalk），记录两边的调用。"""
    qq_calls: list[dict] = []
    dt_calls: list[str] = []

    async def fake_qq(content, *, user_id=None, group_id=None):
        qq_calls.append({"content": content, "user_id": user_id})
        return SendResult(ok=True, segments=1)

    async def fake_dt(content):
        dt_calls.append(content)
        if isinstance(backup_ret, Exception):
            raise backup_ret
        return backup_ret if backup_ret is not None else SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_qq)
    monkeypatch.setattr(ob, "get_status",
                        lambda: {"connected": qq_online, "connected_since": 1.0})
    monkeypatch.setattr(dingtalk_push, "send_dingtalk_message", fake_dt)
    monkeypatch.setattr(egress, "_OFFLINE_RETRY_WAIT_S", 0)
    return qq_calls, dt_calls


def _backup_env(monkeypatch, mode: str = "backup", configured: bool = True):
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":42}')
    monkeypatch.setattr(settings, "DINGTALK_PUSH_MODE", mode)
    monkeypatch.setattr(settings, "DINGTALK_PUSH_WEBHOOK",
                        "https://oapi.dingtalk.com/robot/send?access_token=t" if configured else "")
    monkeypatch.setattr(settings, "DINGTALK_PUSH_SECRET", "SECx" if configured else "")


@pytest.mark.asyncio
async def test_backup_mode_not_triggered_when_qq_ok(monkeypatch):
    _backup_env(monkeypatch, "backup")
    qq_calls, dt_calls = _install_channels(monkeypatch, qq_online=True)
    outcome = await egress.push_to_qq("日报")
    assert outcome.pushed_to == "qq" and outcome.backup == ""
    assert len(qq_calls) == 1 and dt_calls == []


@pytest.mark.asyncio
async def test_backup_mode_fires_when_qq_offline(monkeypatch):
    _backup_env(monkeypatch, "backup")
    qq_calls, dt_calls = _install_channels(monkeypatch, qq_online=False)
    outcome = await egress.push_to_qq("告警")
    assert outcome.pushed_to == "failed(channel_offline)"
    assert outcome.backup == "dingtalk"
    assert qq_calls == [] and dt_calls == ["告警"]


@pytest.mark.asyncio
async def test_backup_mode_fires_on_no_target(monkeypatch):
    """QQ_ALERT_TARGET 未配置也是 failed(...)：兜底让告警仍有路（不静默丢）。"""
    _backup_env(monkeypatch, "backup")
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", "")
    _, dt_calls = _install_channels(monkeypatch, qq_online=True)
    outcome = await egress.push_to_qq("告警")
    assert outcome.pushed_to == "failed(no_target)" and outcome.backup == "dingtalk"
    assert dt_calls == ["告警"]


@pytest.mark.asyncio
async def test_always_mode_double_push(monkeypatch):
    _backup_env(monkeypatch, "always")
    qq_calls, dt_calls = _install_channels(monkeypatch, qq_online=True)
    outcome = await egress.push_to_qq("日报")
    assert outcome.pushed_to == "qq" and outcome.backup == "dingtalk"
    assert len(qq_calls) == 1 and dt_calls == ["日报"]


@pytest.mark.asyncio
async def test_off_mode_and_unconfigured_skip_backup(monkeypatch):
    for mode, configured in (("off", True), ("backup", False), ("always", False)):
        _backup_env(monkeypatch, mode, configured=configured)
        _, dt_calls = _install_channels(monkeypatch, qq_online=False)
        outcome = await egress.push_to_qq("告警")
        assert outcome.pushed_to == "failed(channel_offline)" and outcome.backup == ""
        assert dt_calls == []


@pytest.mark.asyncio
async def test_backup_failure_recorded_honestly(monkeypatch):
    _backup_env(monkeypatch, "backup")
    _, dt_calls = _install_channels(monkeypatch, qq_online=False,
                                    backup_ret=SendResult(ok=False, reason="errcode=310000 sign not match"))
    outcome = await egress.push_to_qq("告警")
    assert outcome.pushed_to == "failed(channel_offline)"
    assert outcome.backup.startswith("failed(errcode=310000")


@pytest.mark.asyncio
async def test_eval_suppresses_both_channels(monkeypatch):
    """零外发硬红线（§6.3）：eval 早返回在备推之前——QQ 与钉钉都必须零调用。"""
    _backup_env(monkeypatch, "always")  # even always 模式，eval 也全抑制
    qq_calls, dt_calls = _install_channels(monkeypatch, qq_online=True)
    ctx = egress.EgressContext(eval_mode=True, loop=None)
    outcome = await egress.push_to_qq("告警", ctx=ctx)
    assert outcome.pushed_to == "suppressed(eval)" and outcome.backup == ""
    assert qq_calls == [] and dt_calls == []


# ── push_from_tool 回话如实反映兜底（§6.3） ──────────────────────

@pytest.mark.asyncio
async def test_push_from_tool_backup_message(monkeypatch):
    """主失败 + 备推送达 → 工具回话必须同时说明两件事（不让 agent 误报全失败）。"""
    import asyncio

    _backup_env(monkeypatch, "backup")
    _install_channels(monkeypatch, qq_online=False)  # 主失败、备推成功

    async with egress.egress_scope(eval_mode=False, run_label="unit") as ctx:
        result = await asyncio.to_thread(egress.push_from_tool, "告警")
    assert "QQ 主通道推送失败" in result and "钉钉" in result and "兜底" in result
    assert ctx.outcome is not None
    assert ctx.outcome.pushed_to == "failed(channel_offline)"
    assert ctx.outcome.backup == "dingtalk"
