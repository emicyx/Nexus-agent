"""/metrics 渲染单测（backend/app/core/metrics.py，上线欠账 B3）。"""
from app.core import metrics, token_budget
from app.core.metrics import record_tool_call, render_metrics


def setup_function(_):
    metrics.reset_for_tests()
    token_budget.reset_for_tests()


def teardown_function(_):
    metrics.reset_for_tests()
    token_budget.reset_for_tests()


def test_render_contains_tool_counters_and_latency():
    record_tool_call("fetch_url", 0.5, ok=True)
    record_tool_call("fetch_url", 1.5, ok=True)
    record_tool_call("fetch_url", 0.1, ok=False)
    out = render_metrics()
    assert 'tool_calls_total{tool="fetch_url"} 3' in out
    assert 'tool_errors_total{tool="fetch_url"} 1' in out
    assert "tool_latency_p95_seconds" in out
    # P95（3 个样本的 95 分位 = 最大值）
    for line in out.splitlines():
        if line.startswith('tool_latency_p95_seconds{tool="fetch_url"}'):
            assert float(line.rsplit(" ", 1)[1]) == 1.5


def test_render_contains_token_metrics():
    token_budget.record_usage(10, 20)
    out = render_metrics()
    assert "token_usage_today_total 30" in out
    assert "token_budget_limit" in out


def test_render_contains_sse_and_dropped():
    out = render_metrics()
    assert "sse_active_connections 0" in out
    assert "sse_dropped_events_total" in out
    # Prometheus 文本格式：HELP/TYPE 行成对出现
    assert out.startswith("# HELP ")


def test_label_escaping():
    # 工具名/会话名含引号、反斜杠、换行时不得破坏文本格式
    record_tool_call('we"ird\\tool', 0.1)
    out = render_metrics()
    assert 'tool="we\\"ird\\\\tool"' in out
