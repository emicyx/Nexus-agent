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

    monkeypatch.setattr(ng.requests, "get", fake_get)
    with pytest.raises(ValueError, match="非公网"):
        safe_get_with_redirects("http://93.184.216.34/a")


def test_redirect_chain_public_ok(monkeypatch):
    def fake_get(url, **kwargs):
        if url == "http://93.184.216.34/a":
            return _FakeResp(302, location="/b")  # 相对 Location
        if url == "http://93.184.216.34/b":
            return _FakeResp(302, location="http://8.8.8.8/c")
        return _FakeResp(200, text="final")

    monkeypatch.setattr(ng.requests, "get", fake_get)
    resp = safe_get_with_redirects("http://93.184.216.34/a")
    assert resp.status_code == 200
    assert resp.text == "final"


def test_too_many_redirects_rejected(monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResp(302, location=url + "x")

    monkeypatch.setattr(ng.requests, "get", fake_get)
    with pytest.raises(ValueError, match="重定向次数"):
        safe_get_with_redirects("http://93.184.216.34/a", max_redirects=3)
