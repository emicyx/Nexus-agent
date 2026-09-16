"""S2 定时任务与推送到货单测：http_check / push_message / egress / job 校验与纯函数。

安全不变量锚点：
- 不变量 1：push_message 参数只有 content（schema 无目标字段）
- 不变量 3：http_check 参数只有 name（schema 无 URL 字段）
- 不变量 2（§5.6）：eval 上下文 / NEXUS_EVAL_MODE 下 egress 一律抑制
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services import egress
from app.tools.http_check_tool import (
    HttpCheckInput,
    HttpCheckTool,
    check_all_targets,
    format_results,
    parse_monitor_targets,
)
from app.tools.push_message_tool import PushMessageInput, PushMessageTool


# ── http_check：目标锁定（安全不变量 3） ──────────────────────────

def test_http_check_schema_has_no_url_param():
    fields = set(HttpCheckInput.model_fields.keys())
    assert fields == {"name"}, f"http_check 参数必须只有 name，实际: {fields}"


def test_push_message_schema_has_no_target_param():
    fields = set(PushMessageInput.model_fields.keys())
    assert fields == {"content"}, f"push_message 参数必须只有 content，实际: {fields}"


def test_parse_monitor_targets_valid():
    raw = '[{"name":"api","url":"http://10.0.0.1:8000/health","expect_status":200,"timeout_s":5},{"name":"web","url":"https://example.com"}]'
    targets = parse_monitor_targets(raw)
    assert [t.name for t in targets] == ["api", "web"]
    assert targets[0].expect_status == 200 and targets[0].timeout_s == 5
    # 缺省值：expect 200 / timeout 10
    assert targets[1].expect_status == 200 and targets[1].timeout_s == 10.0


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "{}",
        '[{"name":"a","url":"ftp://x"}]',
        '[{"name":"a","url":"http://x"},{"name":"a","url":"http://y"}]',
        '[{"url":"http://x"}]',
        '[{"name":"a","url":"http://x","timeout_s":0}]',
    ],
)
def test_parse_monitor_targets_rejects_bad_config(raw):
    with pytest.raises(ValueError):
        parse_monitor_targets(raw)


class _FakeResp:
    def __init__(self, status):
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_check_all_targets_ok_unexpected_timeout(monkeypatch):
    import app.tools.http_check_tool as mod

    monkeypatch.setattr(mod.settings, "MONITOR_TARGETS",
                        '[{"name":"ok","url":"http://ok/","expect_status":200},'
                        '{"name":"weird","url":"http://w/","expect_status":204},'
                        '{"name":"slow","url":"http://s/","timeout_s":1}]')

    def fake_get(url, timeout=None, stream=False, headers=None):
        if url == "http://ok/":
            return _FakeResp(200)
        if url == "http://w/":
            return _FakeResp(200)  # 200 但期望 204 → DOWN
        import requests

        raise requests.ConnectTimeout("boom")

    monkeypatch.setattr(mod.requests, "get", fake_get)
    results = check_all_targets()
    by_name = {r["name"]: r for r in results}
    assert by_name["ok"]["up"] is True and by_name["ok"]["status"] == 200
    assert by_name["ok"]["latency_ms"] >= 0
    assert by_name["weird"]["up"] is False and by_name["weird"]["status"] == 200
    assert by_name["slow"]["up"] is False and "ConnectTimeout" in by_name["slow"]["error"]


def test_check_all_targets_name_filter(monkeypatch):
    import app.tools.http_check_tool as mod

    monkeypatch.setattr(mod.settings, "MONITOR_TARGETS",
                        '[{"name":"a","url":"http://a/"},{"name":"b","url":"http://b/"}]')
    called = []

    def fake_get(url, timeout=None, stream=False, headers=None):
        called.append(url)
        return _FakeResp(200)

    monkeypatch.setattr(mod.requests, "get", fake_get)
    results = check_all_targets("b")
    assert called == ["http://b/"]
    assert len(results) == 1 and results[0]["name"] == "b"


def test_check_all_targets_bad_config_deterministic(monkeypatch):
    import app.tools.http_check_tool as mod

    monkeypatch.setattr(mod.settings, "MONITOR_TARGETS", "broken")
    results = check_all_targets()
    assert len(results) == 1 and results[0]["up"] is False
    assert "MONITOR_TARGETS" in results[0]["error"]


def test_check_all_targets_unknown_name(monkeypatch):
    import app.tools.http_check_tool as mod

    monkeypatch.setattr(mod.settings, "MONITOR_TARGETS", '[{"name":"a","url":"http://a/"}]')
    results = check_all_targets("nope")
    assert results[0]["up"] is False and "nope" in results[0]["error"]


def test_format_results_up_down_lines():
    text = format_results([
        {"name": "a", "up": True, "status": 200, "latency_ms": 12, "url": "http://a/"},
        {"name": "b", "up": False, "status": 500, "expect_status": 200,
         "latency_ms": 30, "url": "http://b/", "error": None},
    ])
    assert "a: UP" in text and "b: DOWN" in text and "500 != 期望 200" in text


# ── egress：目标解析 / eval 抑制 / 断连降级（安全不变量 1/2） ──────

def test_parse_alert_target_valid_private_group_invalid(monkeypatch):
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", "")
    assert egress.parse_alert_target('{"type":"private","user_id":123}') == {
        "type": "private", "user_id": 123}
    assert egress.parse_alert_target('{"type":"group","group_id":456}') == {
        "type": "group", "group_id": 456}
    for bad in ("", None, "xx", "[]", '{"type":"private"}', '{"type":"group","user_id":1}',
                '{"type":"private","user_id":-2}'):
        assert egress.parse_alert_target(bad) is None


@pytest.mark.asyncio
async def test_push_to_qq_eval_suppressed_before_anything(monkeypatch):
    """eval 抑制先于目标解析与渠道检查：未配置目标也记 suppressed(eval)。"""
    import app.channels.onebot_adapter as ob

    sent = []

    async def fake_send(content, *, user_id=None, group_id=None):
        sent.append(content)
        return ob.SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_send)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", "")  # 目标未配置
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)

    ctx = egress.EgressContext(eval_mode=True, loop=asyncio.get_running_loop(), run_label="t")
    outcome = await egress.push_to_qq("告警", ctx=ctx)
    assert outcome.pushed_to == "suppressed(eval)"
    assert sent == [] and ctx.outcome is outcome


@pytest.mark.asyncio
async def test_push_to_qq_global_eval_mode_suppresses(monkeypatch):
    import app.channels.onebot_adapter as ob

    sent = []

    async def fake_send(content, *, user_id=None, group_id=None):
        sent.append(content)
        return ob.SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_send)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":1}')
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", True)

    outcome = await egress.push_to_qq("x")  # 无 ctx，靠全局开关
    assert outcome.pushed_to == "suppressed(eval)" and sent == []


@pytest.mark.asyncio
async def test_push_to_qq_ok_and_offline_retry(monkeypatch):
    import app.channels.onebot_adapter as ob

    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":7}')
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)

    sent = []

    async def fake_send(content, *, user_id=None, group_id=None):
        sent.append((content, user_id, group_id))
        return ob.SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_send)

    # 在线 → 直发
    monkeypatch.setattr(ob, "get_status", lambda: {"connected": True, "connected_since": 1.0})
    outcome = await egress.push_to_qq("hello")
    assert outcome.pushed_to == "qq" and sent == [("hello", 7, None)]

    # 离线 → 等 5s 重查仍离线 → failed(channel_offline)，不静默丢
    monkeypatch.setattr(ob, "get_status", lambda: {"connected": False, "connected_since": None})
    monkeypatch.setattr(egress, "_OFFLINE_RETRY_WAIT_S", 0)
    outcome = await egress.push_to_qq("again")
    assert outcome.pushed_to == "failed(channel_offline)"
    assert len(sent) == 1  # 没有真实发送


@pytest.mark.asyncio
async def test_push_to_qq_no_target(monkeypatch):
    import app.channels.onebot_adapter as ob

    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", "")
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(ob, "get_status", lambda: {"connected": True, "connected_since": 1.0})
    outcome = await egress.push_to_qq("x")
    assert outcome.pushed_to == "failed(no_target)"


@pytest.mark.asyncio
async def test_push_from_tool_outside_job_context():
    """无 job 上下文（普通 Web 会话）→ 拒绝推送，不产生任何发送。"""
    assert egress.current_egress_context() is None
    result = egress.push_from_tool("hi")
    assert result.startswith("错误") and "定时任务" in result


@pytest.mark.asyncio
async def test_push_from_tool_eval_and_ok(monkeypatch):
    """工具线程路径：contextvar 上下文 + run_coroutine_threadsafe 回主循环。"""
    import app.channels.onebot_adapter as ob

    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":9}')
    sent = []

    async def fake_send(content, *, user_id=None, group_id=None):
        sent.append(content)
        return ob.SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_send)

    async def scenario(eval_mode: bool):
        async with egress.egress_scope(eval_mode, run_label="unit") as ctx:
            # to_thread 模拟工具线程：contextvar 复制（同 crewai_async_patch）
            result = await asyncio.to_thread(egress.push_from_tool, "msg")
            return result, ctx

    result, ctx = await scenario(True)
    assert "抑制" in result and sent == []
    assert ctx.outcome.pushed_to == "suppressed(eval)"

    monkeypatch.setattr(ob, "get_status", lambda: {"connected": True, "connected_since": 1.0})
    result, ctx = await scenario(False)
    assert "推送成功" in result and sent == ["msg"]
    assert ctx.outcome.pushed_to == "qq"


# ── push_message 工具：截断 / 空内容 ──────────────────────────────

def test_push_message_truncates(monkeypatch):
    monkeypatch.setattr(settings, "QQ_MESSAGE_MAX_LEN", 10)
    tool = PushMessageTool()
    captured = []

    def fake_push(content):
        captured.append(content)
        return "✅ 推送成功（1 段，目标来自系统配置）。请基于推送结果给出简短结论，不要重复推送。"

    monkeypatch.setattr("app.services.egress.push_from_tool", fake_push)
    result = tool._run("x" * 30)
    assert len(captured[0]) == 10
    assert "截断" in result


def test_push_message_empty_content():
    result = PushMessageTool()._run("   ")
    assert result.startswith("错误")


# ── job 校验与纯函数 ──────────────────────────────────────────────

def test_validate_trigger_cron_interval():
    from app.services import job_service

    job_service.validate_trigger("cron", {"expr": "0 8 * * *"})
    job_service.validate_trigger("interval", {"seconds": 1800})
    with pytest.raises(ValueError):
        job_service.validate_trigger("daily", {})
    with pytest.raises(ValueError):
        job_service.validate_trigger("cron", {"expr": "0 8 * *"})
    with pytest.raises(ValueError):
        job_service.validate_trigger("cron", {"expr": "not a cron"})
    with pytest.raises(ValueError):
        job_service.validate_trigger("interval", {"seconds": 0})
    with pytest.raises(ValueError):
        job_service.validate_trigger("interval", {})


def test_validate_output_config():
    from app.services import job_service

    job_service.validate_output_config({})
    job_service.validate_output_config({"push": {"on": "state_change"}})
    with pytest.raises(ValueError):
        job_service.validate_output_config({"push": {"on": "whenever"}})


def test_unknown_placeholders():
    from app.services import job_service

    t = "{{date}} {{targets_report}} {{kb_delta}} {{typo}}"
    assert job_service.unknown_placeholders(t) == ["{{typo}}"]
    assert job_service.unknown_placeholders("plain") == []


def test_diff_states():
    from app.services.job_runner import diff_states

    prev = {"api": {"up": True}, "db": {"up": False}}
    cur = [
        {"name": "api", "up": False},
        {"name": "db", "up": False},
        {"name": "new", "up": True},
    ]
    assert diff_states(prev, cur) == ["api: up→down"]
    assert diff_states(None, cur) == []  # 首次基线
    assert diff_states({}, cur) == []
    cur2 = [{"name": "db", "up": True}]
    assert diff_states(prev, cur2) == ["db: down→up"]


def test_compute_push_expected():
    from app.services.job_runner import compute_push_expected

    assert compute_push_expected("state_change", ["a: up→down"]) is True
    assert compute_push_expected("state_change", []) is False
    assert compute_push_expected("always", []) is True
    assert compute_push_expected("daily_summary", []) is True


def test_render_input_template_and_directive():
    from app.services.job_runner import build_push_directive, render_input_template

    out = render_input_template(
        "日期：{{date}}\n{{targets_report}}\n保留：{{custom}}",
        {"{{date}}": "2026-09-16", "{{targets_report}}": "ALL UP"},
    )
    assert "2026-09-16" in out and "ALL UP" in out and "{{custom}}" in out

    d_push = build_push_directive(True)
    assert "需要推送" in d_push and "push_message" in d_push
    d_no = build_push_directive(False)
    assert "严禁调用 push_message" in d_no


def test_job_outcome_events_tail_truncation():
    from app.crews.factory import JobOutcome
    from app.core.events import AgentEvent

    oc = JobOutcome()
    for i in range(50):
        oc.push_event(AgentEvent(type="tool_call", content=f"事件{i}" * 100, tool="http_check"))
    assert len(oc.events_tail) == JobOutcome._TAIL_LIMIT
    assert len(oc.events_tail[-1]["excerpt"]) <= JobOutcome._EXCERPT_CHARS
    assert oc.events_tail[-1]["excerpt"].startswith("事件49")


def test_build_trigger_cron_and_interval():
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from app.services import job_scheduler

    t = job_scheduler.build_trigger("cron", {"expr": "0 8 * * *"})
    assert isinstance(t, CronTrigger)
    assert str(t.timezone) == settings.JOB_TIMEZONE
    t2 = job_scheduler.build_trigger("interval", {"seconds": 30})
    assert isinstance(t2, IntervalTrigger)
    with pytest.raises(ValueError):
        job_scheduler.build_trigger("weird", {})


def test_render_kb_delta_and_targets_report():
    from app.services.job_runner import render_targets_report
    from app.services.kb_delta import render_kb_delta

    empty = {"since": "2026-09-15T21:00:00+00:00", "count": 0, "groups": {}}
    assert "无新增" in render_kb_delta(empty)

    delta = {
        "since": "2026-09-15T21:00:00+00:00",
        "count": 2,
        "groups": {
            "text": [{"id": 1, "name": "文档A", "created_at": "2026-09-15T13:05:00+00:00",
                      "chunk_count": 3, "head_preview": "头部预览内容"}],
            "web": [{"id": 2, "name": "网页B", "created_at": "2026-09-15T14:00:00+00:00",
                     "chunk_count": 5, "head_preview": "网页内容"}],
        },
    }
    text = render_kb_delta(delta)
    assert "新增 2 篇" in text and "《文档A》" in text and "《网页B》" in text
    assert "头部预览内容" in text

    report = render_targets_report(
        [{"name": "api", "up": False, "status": 500, "expect_status": 200,
          "latency_ms": 20, "error": None, "url": "http://api/"}],
        changes=["api: up→down"],
        prev_targets={"api": {"up": True}},
    )
    assert "api: DOWN" in report and "api: up→down" in report
    assert "上次成功巡检状态" in report
