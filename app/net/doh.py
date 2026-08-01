"""DoH 解析回退：系统 DNS 失败时通过 DNS-over-HTTPS 查询，带 TTL 缓存。

用法：
    from app.net import doh
    doh.install()          # 在服务启动时安装（可选）
    ips = doh.resolve("example.com")
"""
import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("doh")

DOH_SERVERS = [
    "https://dns.alidns.com/resolve",
    "https://cloudflare-dns.com/dns-query",
]

_CACHE = {}          # host -> (expire_ts, [ips])
_LOCK = threading.Lock()
_TTL = 300


def _doh_query(host: str) -> list[str]:
    """向 DoH 服务器查询 A 记录，返回 IP 列表。"""
    for base in DOH_SERVERS:
        try:
            url = base + "?" + urllib.parse.urlencode({"name": host, "type": "A"})
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/dns-json",
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            ips = [a.get("data") for a in data.get("Answer", []) if a.get("type") == 1 and a.get("data")]
            ips = [ip for ip in ips if isinstance(ip, str) and ":" not in ip]
            if ips:
                return ips
        except Exception:
            continue
    return []


def resolve(host: str) -> list[str]:
    """解析域名（系统 DNS 优先，失败回退 DoH），返回 IPv4 列表。"""
    host = host.strip().rstrip(".")
    if not host:
        return []
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(host)
        if cached and cached[0] > now:
            return list(cached[1])
    ips = []
    done = []

    def _sys_lookup():
        try:
            infos = _original_getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            done.append(list({i[4][0] for i in infos}))
        except socket.gaierror:
            done.append([])
        except Exception:
            done.append([])

    t = threading.Thread(target=_sys_lookup, daemon=True)
    t.start()
    t.join(timeout=3)
    if done:
        ips = done[0]
    if not ips:
        ips = _doh_query(host)
    if ips:
        with _LOCK:
            _CACHE[host] = (now + _TTL, ips)
    return ips


_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_wrapper(host, port, family=0, type=0, proto=0, flags=0):
    """socket.getaddrinfo 包装：系统解析失败时用 DoH 结果合成地址。"""
    ips = resolve(host)
    if not ips:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    results = []
    for ip in ips:
        results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
    return results


_installed = False


def install(enabled: bool = True):
    """安装全局 getaddrinfo 回退（幂等）。"""
    global _installed
    if not enabled:
        return
    if _installed:
        return
    socket.getaddrinfo = _getaddrinfo_wrapper
    _installed = True
    logger.info("DoH 解析回退已启用")
