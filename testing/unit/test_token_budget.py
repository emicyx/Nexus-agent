"""A2 Token 计量与日预算熔断单测（backend/app/core/token_budget.py）。

Redis 镜像在单测环境连不上默认 REDIS_URL（redis 主机名不可解析），
会走"仅内存"降级路径——这正是要验证的容错行为之一。
"""
import pytest

from app.config import settings
from app.core import token_budget
from app.core.llm_errors import classify_llm_error
from app.core.token_budget import (
    TokenBudgetExceededError,
    bind_token_session,
    ensure_budget_available,
    extract_usage,
    get_session_totals,
    get_today_total,
    record_usage,
    unbind_token_session,
)


@pytest.fixture(autouse=True)
def _isolate():
    token_budget.reset_for_tests()
    yield
    token_budget.reset_for_tests()


def test_record_accumulates_daily_total():
    record_usage(10, 20)
    record_usage(total_tokens=5)
    record_usage(0, 0)  # 空 usage 不计入
    assert get_today_total() == 35


def test_session_attribution_via_contextvar():
    token = bind_token_session("sess-A")
    try:
        record_usage(7, 8)
        record_usage(1, 1)
    finally:
        unbind_token_session(token)
    # 未绑定会话的调用不归集
    record_usage(100, 0)
    assert get_session_totals().get("sess-A") == 17
    assert "sess-A" in get_session_totals()


async def test_budget_disabled_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 0)
    record_usage(1_000_000, 1_000_000)
    # 预算关闭（0）：无论用量多大都不拦
    await ensure_budget_available()


async def test_budget_exceeded_raises_with_friendly_message(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 100)
    record_usage(60, 50)  # 已用 110 > 100
    with pytest.raises(TokenBudgetExceededError) as exc_info:
        await ensure_budget_available()
    assert exc_info.value.budget == 100
    # 走错误分类管道 → budget_exceeded kind + 中文建议
    kind, message = classify_llm_error(exc_info.value)
    assert kind == "budget_exceeded"
    assert "预算" in message


async def test_budget_boundary_not_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 100)
    record_usage(30, 69)  # 99 < 100，仍可用
    await ensure_budget_available()  # 不抛


def test_extract_usage_from_llm_result():
    result = {
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    assert extract_usage(result) == (3, 4, 7)
    assert extract_usage({}) == (0, 0, 0)
    assert extract_usage({"usage": {}}) == (0, 0, 0)


def test_record_never_raises_on_garbage():
    # 计量故障绝不影响业务调用
    record_usage(None, None, None)
    record_usage(-5, -5)


def test_session_totals_bounded(monkeypatch):
    for i in range(token_budget._SESSION_TRACK_LIMIT + 20):
        token = bind_token_session(f"s{i}")
        try:
            record_usage(1, 0)
        finally:
            unbind_token_session(token)
    assert len(get_session_totals()) <= token_budget._SESSION_TRACK_LIMIT


# ---------- 运行中途预算检查点（长跑 Crew 中途熔断） ----------

def test_checkpoint_raises_base_exception_carrier(monkeypatch):
    """超限时检查点抛 BudgetExceededRunError（BaseException，穿透 CrewAI
    工具异常兜底），.original 携带 TokenBudgetExceededError 供分类管道。"""
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 100)
    token_budget.reset_checkpoint_throttle_for_tests()
    record_usage(prompt_tokens=50, completion_tokens=60)  # 内存计数 110

    with pytest.raises(token_budget.BudgetExceededRunError) as ei:
        token_budget.check_budget_checkpoint(force=True)

    assert isinstance(ei.value.original, TokenBudgetExceededError)
    # 分类管道可正常识别（producer 侧转发 .original）
    kind, _ = classify_llm_error(ei.value.original)
    assert kind == "budget_exceeded"


def test_checkpoint_no_raise_when_within_budget(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 1000)
    token_budget.reset_checkpoint_throttle_for_tests()
    record_usage(prompt_tokens=50, completion_tokens=60)
    token_budget.check_budget_checkpoint(force=True)  # 不抛


def test_checkpoint_throttled_skip(monkeypatch):
    """30s 节流：上次检查后未到期时直接返回，不再复查（即使用量已变）。"""
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 100)
    token_budget.reset_checkpoint_throttle_for_tests()
    record_usage(prompt_tokens=50, completion_tokens=60)
    with pytest.raises(token_budget.BudgetExceededRunError):
        token_budget.check_budget_checkpoint(force=True)

    # 模拟用量回落（Redis 不可用时检查点读内存计数）
    day = token_budget._today()
    with token_budget._lock:
        token_budget._daily_totals[day] = 0
    # 未到期（刚检查过）：不抛也不复查
    token_budget.check_budget_checkpoint()
