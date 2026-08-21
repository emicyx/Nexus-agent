"""SSRF 防护（P0-3）：URL 安全校验 + 手动重定向循环。

威胁模型：工具入参来自 LLM（可被网页内容提示注入），fetch_url / navigate
可能被指使访问内网服务或云元数据地址（169.254.169.254）。

防护：
- validate_public_url：scheme 白名单 + 域名解析后逐 IP 检查
  （拒绝 private / loopback / link-local / reserved / multicast / unspecified）
- safe_get_with_redirects：allow_redirects=False 手动跟跳，每一跳重新校验
  （自动跟随重定向时，目标地址不再过校验，等于绕过）
逃生门：SSRF_ALLOW_PRIVATE_NETWORK=true 时放行私网（内网部署场景）。
"""
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import requests

from app.config import settings

logger = logging.getLogger("net_guard")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 5


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_ips(host: str) -> list[str]:
    """解析 hostname 的全部地址（IPv4 + IPv6）。解析失败抛 socket.gaierror。"""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def validate_public_url(url: str) -> str:
    """校验 URL 可安全访问（公网 http/https），通过则原样返回。

    不安全时抛 ValueError（含原因）。DNS 解析出的所有地址都必须是公网地址，
    防止多解析记录中混入内网 IP 的绕过手法。
    """
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"不允许的协议（仅 http/https）: {parts.scheme or '(空)'}")
    host = parts.hostname
    if not host:
        raise ValueError("URL 缺少主机名")

    if settings.SSRF_ALLOW_PRIVATE_NETWORK:
        return url

    # 字面量 IP 直接判断；域名走 DNS 解析后逐 IP 判断
    if host.lower() in ("localhost",) or host.lower().endswith(".localhost"):
        raise ValueError(f"禁止访问本机地址: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # 不是字面量 IP，走 DNS 分支
    if ip is not None:
        if _is_forbidden_ip(ip):
            raise ValueError(f"禁止访问非公网地址: {host}")
        return url
    try:
        ips = _host_ips(host)
    except socket.gaierror as e:
        raise ValueError(f"域名解析失败: {host} ({e})") from e
    for addr in ips:
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])  # 去掉 IPv6 zone id
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            raise ValueError(f"域名 {host} 解析到非公网地址 {addr}，已拒绝")
    return url


def safe_get_with_redirects(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 30.0,
    max_redirects: int = _MAX_REDIRECTS,
) -> requests.Response:
    """requests.get 的 SSRF 安全版：手动跟随重定向，每一跳重新校验目标。"""
    current = validate_public_url(url)
    for _ in range(max_redirects + 1):
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("Location")
        if not location:
            return resp
        # 相对 Location 以当前 URL 为基准补全，再校验
        current = validate_public_url(urljoin(current, location))
    raise ValueError(f"重定向次数超过 {max_redirects} 次，已放弃")
