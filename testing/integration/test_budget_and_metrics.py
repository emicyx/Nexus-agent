"""A2 预算熔断 + B3 /metrics 集成测试。"""
import json

from fastapi.testclient import TestClient

from app.config import settings
from app.core import token_budget


def _parse_sse_events(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def test_chat_rejected_when_daily_budget_exceeded(client: TestClient, monkeypatch):
    """预算耗尽后新请求被拒：SSE 返回 budget_exceeded 错误事件（不调 LLM）。"""
    token_budget.reset_for_tests()
    monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 100)
    # 预灌当日用量：Redis 若可用会同步累计，ensure 优先读 Redis
    token_budget.record_usage(200, 100)
    try:
        with client.stream(
            "POST", "/v1/chat/stream", json={"message": "你好", "single": True}
        ) as resp:
            assert resp.status_code == 200
            text = resp.read().decode("utf-8")
        events = _parse_sse_events(text)
        errors = [e for e in events if e.get("type") == "error"]
        assert errors, f"应返回 error 事件，实际事件序列: {[e.get('type') for e in events]}"
        assert errors[0]["error_kind"] == "budget_exceeded"
        assert "预算" in errors[0]["content"]
        assert events[-1].get("type") == "done"
    finally:
        monkeypatch.setattr(settings, "LLM_TOKEN_DAILY_BUDGET", 0)
        token_budget.reset_for_tests()


def test_metrics_endpoint_exposes_core_gauges(client: TestClient):
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "sse_active_connections" in body
    assert "token_usage_today_total" in body
    # Prometheus 文本格式（工具指标需真实工具调用，单测 test_metrics.py 已覆盖）
    assert body.startswith("# HELP")
