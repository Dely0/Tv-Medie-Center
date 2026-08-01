"""播放页解析器与嗅探：把 HTML 播放页 / 解析器结果转成真实媒体直链。"""
import logging
import re
import urllib.parse

import requests

from config import HLS_PROXY_TIMEOUT, PARSE_SOURCES_FILE
from app.net.headers import build_headers

logger = logging.getLogger("parse")

MEDIA_EXT_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(m3u8|mp4|flv|ts|mkv|m4s)(\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
M3U8_STR_RE = re.compile(r"[\"']([^\"']*\.m3u8[^\"']*)[\"']", re.IGNORECASE)
MP4_STR_RE = re.compile(r"[\"']([^\"']*\.mp4[^\"']*)[\"']", re.IGNORECASE)


def _load_parse_sources() -> list[dict]:
    try:
        with open(PARSE_SOURCES_FILE, "r", encoding="utf-8") as f:
            import json
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _abs(base_url: str, raw: str) -> str:
    try:
        return urllib.parse.urljoin(base_url, raw)
    except Exception:
        return raw


def sniff_play_page(url: str, profile: dict | None = None, timeout: float = 8.0) -> str | None:
    """抓取播放页/解析页，提取真实媒体地址。找不到返回 None。"""
    headers = build_headers(profile, url)
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        logger.debug(f"播放页抓取失败 {url}: {e}")
        return None
    if resp.status_code >= 400:
        return None
    final_url = resp.url or url
    ctype = resp.headers.get("Content-Type", "")
    text = resp.text[:2_000_000]

    # 302/直链媒体
    if re.search(r"\.(m3u8|mp4|flv|ts|mkv)(\?|$)", final_url, re.IGNORECASE):
        return final_url

    # <video src>
    m = re.search(r"<video[^>]+src=[\"']([^\"']+)[\"']", text, re.IGNORECASE)
    if m:
        cand = _abs(final_url, m.group(1))
        if MEDIA_EXT_RE.search(cand) or ".m3u8" in cand.lower():
            return cand

    # m3u8 字符串（含 JS 变量/JSON 字段）
    for m in M3U8_STR_RE.finditer(text):
        cand = m.group(1)
        if cand.startswith("//"):
            cand = "https:" + cand
        if cand.startswith("http"):
            return cand
        if cand.startswith("/"):
            return _abs(final_url, cand)
    # mp4 字符串
    for m in MP4_STR_RE.finditer(text):
        cand = m.group(1)
        if cand.startswith("//"):
            cand = "https:" + cand
        if cand.startswith("http"):
            return cand
        if cand.startswith("/"):
            return _abs(final_url, cand)
    # 通用媒体 URL 正则
    for m in MEDIA_EXT_RE.finditer(text):
        return m.group(0)
    return None


def resolve_play_page(url: str, profile: dict | None = None, timeout: float = 8.0) -> str | None:
    """综合解析：优先本地嗅探；再尝试配置的 jx 解析器。"""
    direct = sniff_play_page(url, profile, timeout)
    if direct:
        return direct
    for parse_src in _load_parse_sources()[:5]:
        try:
            parse_url = parse_src.get("url", "")
            if "?url=" not in parse_url and "{}" not in parse_url:
                continue
            if "?url=" in parse_url:
                target = parse_url + urllib.parse.quote(url, safe="")
            else:
                target = parse_url.replace("{}", urllib.parse.quote(url, safe=""))
            found = sniff_play_page(target, profile, min(timeout, 6))
            if found:
                return found
        except Exception:
            continue
    return None
