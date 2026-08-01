"""按源请求头配置：每个源可配置 UA / Referer / Origin / Cookie 模板。"""
import random

from config import USER_AGENTS


def build_headers(profile: dict | None, url: str = "", default_ua: bool = True) -> dict:
    """根据源配置的 header 模板生成请求头。

    profile 支持字段：ua / user_agent / referer / origin / cookie / headers(dict)
    """
    profile = profile or {}
    headers = {}
    if default_ua:
        headers["User-Agent"] = random.choice(USER_AGENTS)
    ua = profile.get("ua") or profile.get("user_agent")
    if ua:
        headers["User-Agent"] = ua
    referer = profile.get("referer")
    if referer:
        headers["Referer"] = referer
    origin = profile.get("origin")
    if origin:
        headers["Origin"] = origin
    cookie = profile.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    extra = profile.get("headers") or {}
    if isinstance(extra, dict):
        headers.update({k: str(v) for k, v in extra.items()})
    headers.setdefault("Accept", "application/json, text/plain, */*")
    return headers


def needs_proxy(profile: dict | None) -> bool:
    """带 Referer/UA/Cookie 等防盗链头的候选需要走后端代理（浏览器无法直连时）。"""
    profile = profile or {}
    return bool(
        profile.get("referer")
        or profile.get("origin")
        or profile.get("cookie")
        or profile.get("ua")
        or profile.get("user_agent")
        or profile.get("headers")
    )
