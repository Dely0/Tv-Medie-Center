"""本地 HLS / 媒体代理。

- /api/hls-proxy  : 拉取 m3u8 并重写分片地址为代理地址，透传防盗链头，解决跨域/防盗链。
- /api/media-proxy: mp4/flv 等文件的 Range 透传代理。
"""
import logging
import re
import urllib.parse

import requests
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from config import HLS_PROXY_TIMEOUT

logger = logging.getLogger("net_proxy")


def _client_headers(ref: str = "", ua: str = "", origin: str = "", cookie: str = "") -> dict:
    headers = {
        "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Accept": "*/*",
    }
    if ref:
        headers["Referer"] = ref
    if origin:
        headers["Origin"] = origin
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _default_referer(url: str) -> str:
    """防盗链兜底：未指定 Referer 时用上游站点根地址。"""
    try:
        p = urllib.parse.urlparse(url)
        return f"{p.scheme}://{p.netloc}/"
    except Exception:
        return ""


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }


def hls_proxy(url: str, ref: str = "", ua: str = "", origin: str = "", cookie: str = ""):
    """拉取 m3u8（master 或 media），重写其中的相对/绝对地址为本代理地址。"""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "无效地址")
    headers = _client_headers(ref or _default_referer(url), ua, origin, cookie)
    try:
        resp = requests.get(url, headers=headers, timeout=HLS_PROXY_TIMEOUT, stream=True)
    except Exception as e:
        raise HTTPException(502, f"上游请求失败: {e}")
    if resp.status_code >= 400:
        raise HTTPException(502, f"上游 HTTP {resp.status_code}")

    ctype = resp.headers.get("Content-Type", "").lower()
    body = resp.content
    if url.lower().endswith(".m3u8") or "mpegurl" in ctype or body[:20].find(b"#EXTM3U") >= 0:
        text = body.decode("utf-8", "replace")
        if "#EXTM3U" not in text:
            # 可能带 BOM 或前导空行
            text = text.lstrip("\ufeff \t\r\n")
        if "#EXTM3U" not in text:
            raise HTTPException(502, "上游返回的不是有效的 m3u8")

        def _rewrite(line: str) -> str:
            s = line.strip()
            if not s or s.startswith("#"):
                return line
            try:
                abs_url = urllib.parse.urljoin(url, s)
            except Exception:
                return line
            if not abs_url.startswith(("http://", "https://")):
                return line
            q = urllib.parse.urlencode({
                "url": abs_url,
                "ref": ref,
                "ua": ua,
                "origin": origin,
                "cookie": cookie,
            })
            return "/api/hls-proxy?" + q

        lines = [_rewrite(l) for l in text.splitlines()]
        rewritten = "\n".join(lines)
        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={**_cors_headers(), "Cache-Control": "no-store"},
        )

    # 非 m3u8（如 ts 分片）→ 透传
    return _stream(resp, _cors_headers())


def media_proxy(url: str, ref: str = "", ua: str = "", origin: str = "", cookie: str = "",
                range_header: str = ""):
    """mp4/flv 等媒体的 Range 透传代理。"""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "无效地址")
    headers = _client_headers(ref or _default_referer(url), ua, origin, cookie)
    if range_header:
        headers["Range"] = range_header
    try:
        resp = requests.get(url, headers=headers, timeout=HLS_PROXY_TIMEOUT, stream=True)
    except Exception as e:
        raise HTTPException(502, f"上游请求失败: {e}")
    if resp.status_code >= 400 and resp.status_code != 206:
        raise HTTPException(502, f"上游 HTTP {resp.status_code}")
    return _stream(resp, _cors_headers())


def _stream(resp: requests.Response, extra_headers: dict) -> StreamingResponse:
    """把上游响应流式转发。"""
    headers = dict(extra_headers)
    for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition"):
        val = resp.headers.get(key)
        if val:
            headers[key] = val

    def gen():
        try:
            for chunk in resp.iter_content(65536):
                if chunk:
                    yield chunk
        finally:
            resp.close()

    return StreamingResponse(gen(), status_code=resp.status_code, headers=headers)
