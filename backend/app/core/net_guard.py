"""SSRF 防护（P0-3 + 上线欠账 A5）：URL 安全校验 + IP 锁定直连 + 手动重定向循环。

威胁模型：工具入参来自 LLM（可被网页内容提示注入），fetch_url / navigate
可能被指使访问内网服务或云元数据地址（169.254.169.254）。

防护：
- validate_public_url：scheme 白名单 + 域名解析后逐 IP 检查
  （拒绝 private / loopback / link-local / reserved / multicast / unspecified）
- DNS rebinding（A5）：校验时解析一次、请求时再解析的 TOCTOU 窗口会被
  rebinding 攻击利用（校验时返回公网 IP，实际连接时 DNS 换成内网 IP）。
  因此校验通过后立即把「本次解析出的公网 IP」登记为该域名的 pin，
  实际 HTTP 连接由自定义连接类锁定该 IP 直连——域名仍保留在 Host 头、
  SNI 与证书校验里，TLS/虚拟主机行为完全不变。
- safe_get_with_redirects：allow_redirects=False 手动跟跳，每一跳重新校验+锁定
  （自动跟随重定向时，目标地址不再过校验，等于绕过）
逃生门：
- SSRF_EXTRA_ALLOW_CIDRS（推荐）：精确网段白名单，命中放行（P1-8）。
- SSRF_ALLOW_PRIVATE_NETWORK=true：全局放行私网（含云元数据，粗放，慎用）。

已知残余面：Playwright 渲染路径（page.goto）无法注入 pin，用 page.route
请求级守卫逐请求校验（见 playwright_tools._install_page_guard）；
requests 路径已完全闭合。
"""
import ipaddress
import logging
import socket
import threading
from urllib.parse import urljoin, urlsplit

import requests
import urllib3
from urllib3.util import connection as _u3_connection

from app.config import settings

logger = logging.getLogger("net_guard")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 5


def _allowed_networks() -> list:
    """SSRF_EXTRA_ALLOW_CIDRS 解析为网络对象（无效项告警忽略，不炸启动）。"""
    nets = []
    for cidr in settings.SSRF_EXTRA_ALLOW_CIDRS:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("SSRF_EXTRA_ALLOW_CIDRS 配置无效，已忽略: %s", cidr)
    return nets


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # P1-8：白名单网段优先——内网部署只需放行个别网段时，
    # 用精确 CIDR 替代 SSRF_ALLOW_PRIVATE_NETWORK 的全局粗放行
    for net in _allowed_networks():
        if ip in net:
            return False
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


# ── DNS pin 注册表（A5：校验时解析的 IP 锁定为实际连接目标）────────────

_pins_lock = threading.Lock()
_dns_pins: dict[str, str] = {}  # 小写域名 -> 已校验公网 IP


def set_dns_pin(host: str, ip: str) -> None:
    with _pins_lock:
        _dns_pins[host.lower()] = ip


def get_pinned_ip(host: str) -> str | None:
    with _pins_lock:
        return _dns_pins.get((host or "").lower())


def clear_dns_pins() -> None:
    with _pins_lock:
        _dns_pins.clear()


def _pick_pin_ip(ips: list[str]) -> str | None:
    """优先 IPv4（部分环境 IPv6 连通性差），strip IPv6 zone id。"""
    cleaned = [a.split("%")[0] for a in ips]
    for addr in cleaned:
        if "." in addr:
            return addr
    return cleaned[0] if cleaned else None


def validate_public_url(url: str, *, pin: bool = False) -> str:
    """校验 URL 可安全访问（公网 http/https），通过则原样返回。

    不安全时抛 ValueError（含原因）。DNS 解析出的所有地址都必须是公网地址，
    防止多解析记录中混入内网 IP 的绕过手法。

    pin=True（实际发请求前调用）：校验通过的同时把解析出的公网 IP 登记
    为该域名的连接 pin，封堵校验与请求之间的 DNS rebinding 窗口。
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
    if pin:
        # 锁定本次解析的 IP：后续真实连接不再二次解析（rebinding 防护核心）
        set_dns_pin(host, _pick_pin_ip(ips))
    return url


# ── 锁定 IP 的 HTTP 传输层（A5）──────────────────────────────────────


class _PinnedConnectionMixin:
    """TCP 连到 pin 的 IP；Host 头 / SNI / 证书校验仍用原域名（self.host 不变）。"""

    def _new_conn(self) -> socket.socket:
        pin = get_pinned_ip(self._dns_host or self.host)
        if pin is None:
            return super()._new_conn()
        # 字面量 IP 不触发 DNS（OS 数字地址快速路径），无 rebinding 窗口
        return _u3_connection.create_connection(
            (pin, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )


class PinnedHTTPConnection(_PinnedConnectionMixin, urllib3.connection.HTTPConnection):
    pass


class PinnedHTTPSConnection(_PinnedConnectionMixin, urllib3.connection.HTTPSConnection):
    pass


class PinnedHTTPPool(urllib3.HTTPConnectionPool):
    ConnectionCls = PinnedHTTPConnection


class PinnedHTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = PinnedHTTPSConnection


class PinnedDNSAdapter(requests.adapters.HTTPAdapter):
    """把两个 scheme 的连接池都换成「锁定 IP 解析」版本。"""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = urllib3.PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs
        )
        self.poolmanager.pool_classes_by_scheme = {
            "http": PinnedHTTPPool,
            "https": PinnedHTTPSPool,
        }


_tls = threading.local()


def _pinned_session() -> requests.Session:
    """每线程一个带 pin adapter 的 Session（Session 非线程安全，禁跨线程复用）。"""
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        adapter = PinnedDNSAdapter()
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _tls.session = s
    return s


def _http_get(url: str, *, headers: dict | None, timeout: float) -> requests.Response:
    """实际发请求（走 pin 锁定连接）。独立函数便于测试注入。"""
    return _pinned_session().get(url, headers=headers, timeout=timeout, allow_redirects=False)


def safe_get_with_redirects(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 30.0,
    max_redirects: int = _MAX_REDIRECTS,
) -> requests.Response:
    """requests.get 的 SSRF 安全版：手动跟随重定向，每一跳重新校验+锁定目标 IP。"""
    current = validate_public_url(url, pin=True)
    for _ in range(max_redirects + 1):
        resp = _http_get(current, headers=headers, timeout=timeout)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("Location")
        if not location:
            return resp
        # 相对 Location 以当前 URL 为基准补全，再校验
        current = validate_public_url(urljoin(current, location), pin=True)
    raise ValueError(f"重定向次数超过 {max_redirects} 次，已放弃")
