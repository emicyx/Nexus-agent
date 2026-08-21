"""LLM 错误分类单测（core/llm_errors.py，熔断"报错提示方案"）。"""
import pytest

from app.core import llm_errors
from app.core.llm_errors import classify_llm_error


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeHTTPError(Exception):
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.response = _FakeResponse(status_code)


def test_timeout():
    kind, msg = classify_llm_error(TimeoutError("LLM 请求超时（600 秒）"))
    assert kind == llm_errors.KIND_TIMEOUT
    assert "超时" in msg


def test_timeout_by_keyword():
    kind, _ = classify_llm_error(RuntimeError("Request timed out after 30s"))
    assert kind == llm_errors.KIND_TIMEOUT


def test_auth_401():
    kind, msg = classify_llm_error(_FakeHTTPError("401 Client Error", 401))
    assert kind == llm_errors.KIND_AUTH
    assert "QWEN_API_KEY" in msg


def test_rate_limit_429():
    kind, _ = classify_llm_error(_FakeHTTPError("429 Too Many Requests", 429))
    assert kind == llm_errors.KIND_RATE_LIMIT


def test_rate_limit_by_keyword():
    kind, _ = classify_llm_error(RuntimeError("LLM 请求限流: Requests rate limited"))
    assert kind == llm_errors.KIND_RATE_LIMIT


def test_token_limit_400():
    kind, msg = classify_llm_error(
        _FakeHTTPError("400: this model's maximum context length is 8192 tokens", 400)
    )
    assert kind == llm_errors.KIND_TOKEN_LIMIT
    assert "上下文" in msg


def test_server_500():
    kind, _ = classify_llm_error(_FakeHTTPError("500 Server Error", 500))
    assert kind == llm_errors.KIND_SERVER


def test_network_connection_error():
    kind, _ = classify_llm_error(ConnectionError("Failed to establish a new connection"))
    assert kind == llm_errors.KIND_NETWORK


def test_unknown_fallback_keeps_original_text():
    kind, msg = classify_llm_error(ValueError("响应中未找到 choices 字段"))
    assert kind == llm_errors.KIND_UNKNOWN
    assert "choices" in msg


def test_long_raw_text_truncated():
    kind, msg = classify_llm_error(ValueError("x" * 500))
    assert len(msg) < 250  # 建议文案 + 截断后的原始摘要
    assert "..." in msg


def test_all_kinds_have_advice():
    for kind in (
        llm_errors.KIND_TOKEN_LIMIT, llm_errors.KIND_RATE_LIMIT, llm_errors.KIND_AUTH,
        llm_errors.KIND_TIMEOUT, llm_errors.KIND_NETWORK, llm_errors.KIND_SERVER,
        llm_errors.KIND_UNKNOWN,
    ):
        assert llm_errors._USER_ADVICE[kind]
