"""LLM 错误分类与用户友好提示（require.txt 熔断策略的"报错提示方案"补全）。

此前 chat producer 兜底时直接把 str(异常) 塞进 SSE error 事件，用户看到的是
"400 Client Error: Bad Request for url: ..." 这类面向开发者的文案。

本模块按错误根因分类（token 超限 / 限流 / 鉴权 / 超时 / 网络 / 服务端 / 未知），
给出带行动建议的中文提示；kind 一并随 error 事件下发（error_kind 字段），
前端可按 kind 决定是否展示"新建会话""稍后重试"等引导。

识别依据：异常类型（TimeoutError / HTTPError 带 status_code / ConnectionError）
+ 错误文本关键词（DashScope 各类报错串）。文本匹配是启发式的——新错误形态
拿不到 kind 时回退 unknown，仍显示原始信息，不会更糟。
"""
from __future__ import annotations

# 错误类别（下发到前端 error_kind，前端据此展示引导文案）
KIND_TOKEN_LIMIT = "token_limit"
KIND_RATE_LIMIT = "rate_limit"
KIND_AUTH = "auth"
KIND_TIMEOUT = "timeout"
KIND_NETWORK = "network"
KIND_SERVER = "server"
KIND_UNKNOWN = "unknown"

# 每类错误给用户的行动建议
_USER_ADVICE = {
    KIND_TOKEN_LIMIT: "本轮对话已超出模型上下文上限。建议点击「新建对话」重开上下文，或缩短输入/让 Agent 少读长文件。",
    KIND_RATE_LIMIT: "模型服务正在限流。请稍等 30 秒后点击「重试」。",
    KIND_AUTH: "LLM API Key 无效或未开通对应模型权限。请检查 .env 中的 QWEN_API_KEY 及百炼控制台的模型授权。",
    KIND_TIMEOUT: "模型响应超时。可直接「重试」；若持续超时，请缩短问题复杂度或稍后再试。",
    KIND_NETWORK: "无法连接模型服务。请检查本机网络（含代理设置）后重试。",
    KIND_SERVER: "模型服务端暂时不可用。请稍后「重试」。",
    KIND_UNKNOWN: "执行出错，可「重试」；若持续失败请查看后端日志。",
}

# 文本关键词 → 类别（小写匹配）
_TOKEN_LIMIT_HINTS = (
    "context length", "maximum context", "input too long",
    "too many tokens", "exceed", "token limit", "请求长度",
)
_RATE_LIMIT_HINTS = ("throttl", "rate limit", "限流", "请求过于频繁", "quota")
_AUTH_HINTS = ("invalid api-key", "invalid api key", "unauthorized", "authorization", "鉴权", "invalid_authentication")
_NETWORK_HINTS = ("connection", "connect", "网络", "dns", "name or service not known")


def _status_code(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def classify_llm_error(exc: BaseException) -> tuple[str, str]:
    """把异常分类为 (error_kind, 用户提示)。

    用户提示 = 行动建议 + 括号内原始摘要（截断，方便排障对照后端日志）。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    code = _status_code(exc)

    kind = KIND_UNKNOWN
    if (
        isinstance(exc, TimeoutError)
        or "timeout" in text or "timed out" in text or "超时" in text
    ):
        kind = KIND_TIMEOUT
    elif code in (401, 403) or any(h in text for h in _AUTH_HINTS):
        kind = KIND_AUTH
    elif code == 429 or any(h in text for h in _RATE_LIMIT_HINTS):
        kind = KIND_RATE_LIMIT
    elif code == 400 and any(h in text for h in _TOKEN_LIMIT_HINTS):
        kind = KIND_TOKEN_LIMIT
    elif (code is not None and code >= 500) or "server error" in text or "服务器错误" in text:
        kind = KIND_SERVER
    elif isinstance(exc, (ConnectionError, OSError)) or any(h in text for h in _NETWORK_HINTS):
        kind = KIND_NETWORK
    elif any(h in text for h in _TOKEN_LIMIT_HINTS):
        # 无 status_code 的文本形态（部分 SDK 会把 400 包装掉）
        kind = KIND_TOKEN_LIMIT

    advice = _USER_ADVICE[kind]
    raw = str(exc).strip()
    if raw and len(raw) > 160:
        raw = raw[:160] + "..."
    return kind, f"{advice}（{raw}）" if raw else advice
