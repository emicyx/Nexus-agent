"""AliyunLLM 适配层单测（backend/app/llm/aliyun_llm.py）。

全部 Mock requests，零真实网络调用。
"""
import json

import pytest

from app.llm.aliyun_llm import AliyunLLM


class FakeResponse:
    """伪造 requests.Response：可迭代行 / 状态码 / JSON。"""

    def __init__(self, status_code=200, lines=None, json_body=None, text=""):
        self.status_code = status_code
        self._lines = lines or []
        self._json_body = json_body
        self.text = text
        self.url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        self.headers = {}

    def iter_lines(self, decode_unicode=True):
        for ln in self._lines:
            yield ln

    def json(self):
        return self._json_body

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}: {self.text[:200]}")


class FakeSession:
    """伪造 requests.Session：按队列依次返回响应。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    def head(self, url, timeout=10):
        return FakeResponse(status_code=200)


class TokenRecorder:
    """记录 ctx.on_token 收到的 token。"""

    def __init__(self):
        self.tokens = []

    def on_token(self, token):
        self.tokens.append(token)


def _make_llm():
    return AliyunLLM(model="qwen-plus", api_key="sk-test", timeout=5, retry_count=1)


# ---------- 构造 ----------

def test_constructor_requires_api_key(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        AliyunLLM(model="qwen-plus")


def test_constructor_invalid_region():
    with pytest.raises(ValueError):
        AliyunLLM(model="qwen-plus", api_key="sk-test", region="mars")


# ---------- 多模态归一化 ----------

def test_normalize_function_calling_base64_in_tool_msg():
    llm = _make_llm()
    data_url = "data:image/png;base64,iVBORw0KGgo="
    messages = [
        {"role": "user", "content": "请分析图片"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "call_1", "content": f"prefix{data_url}"},
        {"role": "user", "content": "图片已给，请回答"},
    ]
    out, flag = llm._normalize_multimodal_tool_result(messages)
    assert flag is True
    # 图片被注入到 user 消息的多模态 content
    user_msg = out[-1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    blocks = {b["type"] for b in user_msg["content"]}
    assert "image_url" in blocks
    # tool 消息内容被替换为占位文案
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert "图片内容已加载" in tool_msgs[0]["content"]


def test_normalize_flush_when_tool_is_last():
    llm = _make_llm()
    data_url = "data:image/png;base64,AAAA"
    messages = [
        {"role": "user", "content": "看图"},
        {"role": "tool", "tool_call_id": "c1", "content": data_url},
    ]
    out, flag = llm._normalize_multimodal_tool_result(messages)
    assert flag is True
    assert out[-1]["role"] == "user"  # 合成 user 消息
    assert any(b["type"] == "image_url" for b in out[-1]["content"])


def test_normalize_plain_text_unchanged():
    llm = _make_llm()
    messages = [{"role": "user", "content": "你好"}]
    out, flag = llm._normalize_multimodal_tool_result(messages)
    assert flag is False
    assert out == messages


# ---------- 流式累积 ----------

def test_streaming_content_tokens_pushed_and_returned():
    llm = _make_llm()
    lines = [
        'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"好"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    session = FakeSession([FakeResponse(status_code=200, lines=lines)])
    recorder = TokenRecorder()
    ctx = _DummyCtx(recorder)
    result = llm._call_streaming({"model": "qwen-plus"}, session, ctx)
    assert result == "你好"
    assert recorder.tokens == ["你", "好"]


def test_streaming_tool_calls_arguments_accumulate_across_chunks():
    llm = _make_llm()
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_x","type":"function",'
        '"function":{"name":"search","arguments":""}}]},"finish_reason":null}]}',
        # arguments 跨多个 chunk 累积：'{"query":"' + '天气' + '"}' = '{"query":"天气"}'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"query\\":\\""}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"天气"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"}"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    session = FakeSession([FakeResponse(status_code=200, lines=lines)])
    recorder = TokenRecorder()
    ctx = _DummyCtx(recorder)
    result = llm._call_streaming({"model": "qwen-plus"}, session, ctx)
    assert isinstance(result, dict)
    tc = result["choices"][0]["message"]["tool_calls"]
    assert tc[0]["function"]["name"] == "search"
    assert tc[0]["id"] == "call_x"
    # arguments 跨 chunk 累积成合法 JSON
    assert json.loads(tc[0]["function"]["arguments"]) == {"query": "天气"}


def test_streaming_content_and_tool_calls_mutually_exclusive():
    """出现 tool_calls 后，content 不再推送 token。"""
    llm = _make_llm()
    lines = [
        'data: {"choices":[{"delta":{"content":"思考"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"search","arguments":"{}"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"不应出现"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    ]
    session = FakeSession([FakeResponse(status_code=200, lines=lines)])
    recorder = TokenRecorder()
    ctx = _DummyCtx(recorder)
    result = llm._call_streaming({"model": "qwen-plus"}, session, ctx)
    assert isinstance(result, dict)
    # tool_calls 出现后不再流式推送 content token（互斥）
    assert recorder.tokens == ["思考"]


# ---------- 非流式重试 ----------

def test_do_call_retries_5xx_then_succeeds():
    llm = _make_llm()  # retry_count=1
    ok_body = {"choices": [{"message": {"content": "ok"}}]}
    session = FakeSession([
        FakeResponse(status_code=500, text="boom"),
        FakeResponse(status_code=200, json_body=ok_body),
    ])
    llm._session = session  # 注入 FakeSession，避免真实网络
    result = llm._do_call({"model": "qwen-plus", "messages": []})
    assert result["choices"][0]["message"]["content"] == "ok"
    assert len(session.post_calls) == 2


def test_do_call_429_then_succeeds():
    llm = _make_llm()
    ok_body = {"choices": [{"message": {"content": "ok"}}]}
    session = FakeSession([
        FakeResponse(status_code=429, text="rate limited"),
        FakeResponse(status_code=200, json_body=ok_body),
    ])
    llm._session = session
    result = llm._do_call({"model": "qwen-plus", "messages": []})
    assert result["choices"][0]["message"]["content"] == "ok"


def test_do_call_all_5xx_raises():
    llm = _make_llm()  # retry_count=1 → 最多 2 次尝试
    session = FakeSession([
        FakeResponse(status_code=500, text="err1"),
        FakeResponse(status_code=500, text="err2"),
    ])
    llm._session = session
    with pytest.raises(RuntimeError):
        llm._do_call({"model": "qwen-plus", "messages": []})
    assert len(session.post_calls) == 2  # retry_count+1


def test_call_empty_content_fuse():
    """空响应熔断：连续空内容最终抛 ValueError。"""
    llm = _make_llm()
    ok_body = {"choices": [{"message": {"content": ""}}]}
    session = FakeSession([FakeResponse(status_code=200, json_body=ok_body)])
    llm._session = session  # 直接注入 session 避免 _get_session 新建
    with pytest.raises(ValueError):
        llm.call([{"role": "user", "content": "hi"}], _retry_on_empty=True)


class _DummyCtx:
    """简化版流式上下文，用 recorder 记录 token。"""

    def __init__(self, recorder):
        self._recorder = recorder

    def on_token(self, token):
        self._recorder.on_token(token)
