"""TVBox 标准 JSON 配置解析与社区源提取。

支持：
- 直接 JSON
- 图片头 + JSON（常见防抓取技巧）
- 图片头 + base64 载荷
"""
import base64
import json
import logging
import re
import time
import urllib.request

import config as cfg

logger = logging.getLogger("tvbox_config")


def fetch_tvbox_config(url: str, timeout: float = 20.0) -> dict | None:
    """拉取并解析 TVBox 配置 JSON（兼容图片前缀技巧）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return parse_config_bytes(data)


def parse_config_bytes(data: bytes) -> dict | None:
    text = data.decode("utf-8", "replace")
    # 1. 直接 JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. 图片头 + JSON
    idx = text.find("{")
    if idx > 0:
        tail = text[idx:]
        try:
            return json.loads(tail)
        except Exception:
            pass

    # 3. 图片头 + base64 载荷
    m = re.search(r"[A-Za-z0-9+/=]{500,}", text)
    if m:
        b64 = re.sub(r"\s+", "", m.group(0))
        try:
            raw = base64.b64decode(b64)
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            pass
    return None


def _base_url_from_api(api: str) -> str:
    """从 MacCMS API 地址推导站点根地址。"""
    if not api:
        return ""
    m = re.match(r"(https?://[^/]+)", api)
    if not m:
        return ""
    return m.group(1)


def extract_maccms_entries(sites: list, source_url: str = "") -> list[dict]:
    """提取 TVBox 配置中 type 0/1 的 MacCMS 站点，转为本项目源配置格式。"""
    entries = []
    for s in sites or []:
        if not isinstance(s, dict):
            continue
        try:
            stype = int(s.get("type", -1))
        except (TypeError, ValueError):
            continue
        if stype not in (0, 1):
            continue
        api = (s.get("api") or "").strip()
        name = (s.get("name") or s.get("key") or "").strip()
        base_url = _base_url_from_api(api)
        if not name or not base_url:
            continue
        entries.append({
            "name": name,
            "base_url": base_url,
            "enabled": True,
            "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
            "source_type": "maccms",
            "from_config": source_url,
        })
    return entries


def extract_parse_entries(parses: list) -> list[dict]:
    """提取 TVBox 配置中的解析器。"""
    entries = []
    for p in parses or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        url = (p.get("url") or "").strip()
        if name and url.startswith("http"):
            entries.append({"name": name, "url": url, "type": p.get("type", 0)})
    return entries


def merge_community_sources(entries: list[dict]) -> tuple[int, int]:
    """与现有社区源合并（按 base_url 去重），写入 community 配置文件。"""
    existing = {"_version": 0, "_updated_at": "", "_from": [], "sources": []}
    try:
        with open(cfg.COMMUNITY_SOURCES_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        pass

    seen = set()
    merged = []
    for s in existing.get("sources", []):
        key = (s.get("base_url") or "").rstrip("/")
        if key:
            seen.add(key)
            merged.append(s)

    added = 0
    for e in entries:
        key = (e.get("base_url") or "").rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(e)
        added += 1

    existing["_version"] = int(existing.get("_version") or 0) + 1
    existing["_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    existing["sources"] = merged
    try:
        with open(cfg.COMMUNITY_SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"社区源写入失败: {e}")
    return added, len(merged)


def save_parse_sources(entries: list[dict]) -> int:
    """保存解析器配置（去重合并）。"""
    existing = []
    try:
        with open(cfg.PARSE_SOURCES_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        pass
    seen = set()
    merged = []
    for p in existing:
        key = (p.get("url") or "").strip()
        if key:
            seen.add(key)
            merged.append(p)
    added = 0
    for p in entries:
        key = (p.get("url") or "").strip()
        if key in seen:
            continue
        seen.add(key)
        merged.append(p)
        added += 1
    try:
        with open(cfg.PARSE_SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"解析器配置写入失败: {e}")
    return added
