"""SSRF 防护单测（P0-3，backend/app/core/net_guard.py）。

域名解析 mock 掉 _host_ips，不发真实 DNS 请求。
"""
import pytest

import app.core.net_guard as ng
from app.config import settings
from app.core.net_guard import safe_get_with_redirects, validate_public_url


# ---------- validate_public_url：字面量 IP / 协议 / 主机名 ----------

def test_scheme_ftp_rejected():
    with pytest.raises(ValueError, match="协议"):
        validate_public_url("ftp://example.com/file")


def test_localhost_rejected():
    with pytest.raises(ValueError, match="本机"):
        validate_public_url("http://localhost:8000/health")


def test_loopback_literal_rejected():
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://127.0.0.1/x")


def test_link_local_metadata_rejected():
    # 云元数据地址是最典型的 SSRF 目标
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


def test_private_literal_rejected():
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://10.0.0.5/x")


def test_ipv6_loopback_rejected():
    with pytest.raises(ValueError):
        validate_public_url("http://[::1]/x")


def test_public_literal_allowed():
    assert validate_public_url("http://8.8.8.8/x") == "http://8.8.8.8/x"


def test_missing_host_rejected():
    with pytest.raises(ValueError):
        validate_public_url("http:///path")


# ---------- validate_public_url：域名走 DNS 解析 ----------

def test_domain_resolving_private_rejected(monkeypatch):
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["10.1.2.3"])
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://evil.example.com/x")


def test_domain_mixed_records_any_private_rejected(monkeypatch):
    # 多条解析记录里混入一条内网地址也要拒绝
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["93.184.216.34", "192.168.1.1"])
    with pytest.raises(ValueError):
        validate_public_url("http://mixed.example.com/x")


def test_domain_public_allowed(monkeypatch):
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["93.184.216.34"])
    assert validate_public_url("https://example.com/") == "https://example.com/"


def test_domain_dns_failure_rejected(monkeypatch):
    import socket

    def boom(host):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(ng, "_host_ips", boom)
    with pytest.raises(ValueError, match="解析失败"):
        validate_public_url("http://nonexistent.invalid/x")


def test_allow_private_network_escape_hatch(monkeypatch):
    monkeypatch.setattr(settings, "SSRF_ALLOW_PRIVATE_NETWORK", True)
    assert validate_public_url("http://10.0.0.5/x") == "http://10.0.0.5/x"


# ---------- safe_get_with_redirects：重定向逐跳校验 ----------

class _FakeResp:
    def __init__(self, status_code, location=None, text="ok"):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.text = text


def test_redirect_to_private_rejected(monkeypatch):
    # 公网页面 302 跳内网：自动跟随会绕过入口校验，必须逐跳拦截
    # 用字面量公网 IP 起点，避免测试依赖真实 DNS
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == "http://93.184.216.34/a":
            return _FakeResp(302, location="http://127.0.0.1:8000/steal")
        return _FakeResp(200)

    monkeypatch.setattr(ng, "_http_get", fake_get)
    with pytest.raises(ValueError, match="非公网"):
        safe_get_with_redirects("http://93.184.216.34/a")


def test_redirect_chain_public_ok(monkeypatch):
    def fake_get(url, **kwargs):
        if url == "http://93.184.216.34/a":
            return _FakeResp(302, location="/b")  # 相对 Location
        if url == "http://93.184.216.34/b":
            return _FakeResp(302, location="http://8.8.8.8/c")
        return _FakeResp(200, text="final")

    monkeypatch.setattr(ng, "_http_get", fake_get)
    resp = safe_get_with_redirects("http://93.184.216.34/a")
    assert resp.status_code == 200
    assert resp.text == "final"


def test_too_many_redirects_rejected(monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResp(302, location=url + "x")

    monkeypatch.setattr(ng, "_http_get", fake_get)
    with pytest.raises(ValueError, match="重定向次数"):
        safe_get_with_redirects("http://93.184.216.34/a", max_redirects=3)


# ---------- A5：DNS rebinding 防护（校验后锁定 IP 直连）----------


def test_validate_with_pin_registers_ip(monkeypatch):
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["93.184.216.34", "1.2.3.4"])
    ng.clear_dns_pins()
    try:
        validate_public_url("https://Example.COM/x", pin=True)
        # 优先 IPv4；key 为小写域名
        assert ng.get_pinned_ip("example.com") == "93.184.216.34"
    finally:
        ng.clear_dns_pins()


def test_validate_without_pin_does_not_register(monkeypatch):
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["93.184.216.34"])
    ng.clear_dns_pins()
    try:
        validate_public_url("https://example.com/x")
        assert ng.get_pinned_ip("example.com") is None
    finally:
        ng.clear_dns_pins()


def test_pin_refuses_private_resolution(monkeypatch):
    # pin 模式下解析到私网 IP 同样被拒（与普通校验一致）
    monkeypatch.setattr(ng, "_host_ips", lambda host: ["10.0.0.8"])
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("https://rebind.example.com/x", pin=True)


def test_pinned_connection_targets_pinned_ip():
    """自定义连接类的 TCP 目标 = pin 的 IP（Host/SNI 仍用域名）。"""
    ng.clear_dns_pins()
    try:
        ng.set_dns_pin("example.com", "93.184.216.34")
        pool_cls = ng.PinnedHTTPSPool
        conn = pool_cls.ConnectionCls(host="example.com", port=443)
        conn.timeout = 5

        calls = []

        def fake_create(address, *args, **kwargs):
            calls.append(address)
            import socket as _s

            return _s.socket()

        original = ng._u3_connection.create_connection
        ng._u3_connection.create_connection = fake_create
        try:
            conn._new_conn()
        finally:
            ng._u3_connection.create_connection = original

        assert calls == [("93.184.216.34", 443)]
        # 域名保留在连接对象上（Host 头 / SNI / 证书校验用）
        assert conn.host == "example.com"
    finally:
        ng.clear_dns_pins()


def test_unpinned_host_falls_back_to_normal_resolution():
    """未登记 pin 的域名走父类默认解析（兼容非 safe_get 路径的普通请求）。"""
    ng.clear_dns_pins()
    conn = ng.PinnedHTTPConnection(host="not-pinned.example.com")
    conn.timeout = 5

    called = []

    def fake_super_new_conn():
        called.append(True)
        import socket as _s

        return _s.socket()

    original = ng.urllib3.connection.HTTPConnection._new_conn
    ng.urllib3.connection.HTTPConnection._new_conn = lambda self: fake_super_new_conn()
    try:
        conn._new_conn()
        assert called == [True]
    finally:
        ng.urllib3.connection.HTTPConnection._new_conn = original


# ---------- P1-8：SSRF_EXTRA_ALLOW_CIDRS 网段白名单 ----------

def test_cidr_allowlist_permits_whitelisted_private(monkeypatch):
    """白名单网段内的私网地址放行（内网部署只放行个别网段的精确方式）。"""
    monkeypatch.setattr(settings, "SSRF_EXTRA_ALLOW_CIDRS", ["10.0.1.0/24"])
    assert validate_public_url("http://10.0.1.20:8080/api") == "http://10.0.1.20:8080/api"


def test_cidr_allowlist_still_blocks_others(monkeypatch):
    monkeypatch.setattr(settings, "SSRF_EXTRA_ALLOW_CIDRS", ["10.0.1.0/24"])
    with pytest.raises(ValueError):
        validate_public_url("http://10.0.2.20:8080/api")  # 同为私网但不在白名单
    with pytest.raises(ValueError):
        validate_public_url("http://169.254.169.254/latest/meta-data")  # 云元数据永拒


def test_invalid_cidr_ignored_not_fatal(monkeypatch):
    monkeypatch.setattr(settings, "SSRF_EXTRA_ALLOW_CIDRS", ["not-a-cidr"])
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/x")  # 无效项被忽略，默认拒绝仍生效
