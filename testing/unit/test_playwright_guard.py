"""浏览器 SSRF 守卫的 URL 判定单测（playwright_tools._url_allowed）。

不启动浏览器，只测纯逻辑：http(s) 走 net_guard 校验，其余 scheme 放行
（data:/blob:/about: 是页面内部机制，不构成 SSRF 面）。
"""
from app.tools.playwright_tools import _url_allowed


def test_non_http_schemes_allowed():
    assert _url_allowed("about:blank") is True
    assert _url_allowed("data:text/html,<p>hi</p>") is True
    assert _url_allowed("blob:https://example.com/uuid") is True


def test_private_targets_blocked():
    assert _url_allowed("http://127.0.0.1:8080/") is False
    assert _url_allowed("http://169.254.169.254/latest/meta-data") is False
    assert _url_allowed("http://10.0.0.1/x") is False


def test_public_allowed(monkeypatch):
    import app.core.net_guard as ng

    monkeypatch.setattr(ng, "_host_ips", lambda host: ["93.184.216.34"])
    assert _url_allowed("https://example.com/a?b=1") is True
