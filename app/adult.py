"""成人内容开关与源配置。

默认关闭。想在自己电脑上开启时，创建/编辑 data/adult_config.json：
{
  "enabled": true,
  "sources": [
    {"name": "成人源A", "base_url": "https://lbapi9.com", "category_map": {...}},
    {"name": "成人源B", "base_url": "http://fhapi9.com", "category_map": {...}}
  ]
}
未创建配置文件时使用内置默认源（处于关闭状态，不会加载）。
开启后顶部导航会出现“成人”页面；关闭时该页面隐藏且源不参与搜索/爬取。
"""
import json
import logging
import os
import time

logger = logging.getLogger("adult")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "adult_config.json")

DEFAULT_SOURCES = [
    {
        "name": "成人源A",
        "base_url": "https://lbapi9.com",
        "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
    },
    {
        "name": "成人源B",
        "base_url": "http://fhapi9.com",
        "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
    },
]


def load_config() -> dict:
    """读取成人配置；文件不存在或异常时返回默认关闭配置"""
    data = {"enabled": False, "sources": DEFAULT_SOURCES}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            data["enabled"] = bool(user.get("enabled", False))
            if isinstance(user.get("sources"), list) and user["sources"]:
                data["sources"] = user["sources"]
    except Exception as e:
        logger.warning(f"成人配置读取失败，使用默认: {e}")
    return data


def is_enabled() -> bool:
    return bool(load_config().get("enabled"))


def get_adult_sources() -> list[dict]:
    """开启时返回成人源配置列表；关闭时返回空"""
    cfg = load_config()
    if not cfg.get("enabled"):
        return []
    return cfg.get("sources") or []


def source_names() -> list[str]:
    return [s.get("name", "") for s in get_adult_sources() if s.get("name")]


_sync_state = {"running": False, "count": 0, "last_run": None, "error": None}


def sync_adult_content(pages_per_category: int = 2) -> int:
    """轻量回填成人源列表页到本地库（后台调用，约 1-2 分钟）"""
    if _sync_state["running"]:
        return 0
    _sync_state["running"] = True
    _sync_state["error"] = None
    total = 0
    try:
        # 函数内局部导入，避免与 maccms_source 循环引用
        from app.database import upsert_video
        from app.maccms_source import MaccmsSource
        seen = set()
        for item in get_adult_sources():
            src = MaccmsSource(
                name=item.get("name", "成人源"),
                base_url=item.get("base_url", ""),
                category_map=item.get("category_map"),
            )
            for cat in ("movie", "tv", "variety", "anime"):
                for pg in range(1, pages_per_category + 1):
                    try:
                        items = src.list_page(cat, pg, pagesize=60)
                    except Exception:
                        continue
                    if not items:
                        break
                    for it in items:
                        su = it.get("source_url", "")
                        if not su or su in seen:
                            continue
                        seen.add(su)
                        try:
                            upsert_video(it)
                            total += 1
                        except Exception:
                            pass
        _sync_state["count"] = total
        _sync_state["last_run"] = time.time()
        logger.info(f"成人内容同步完成: {total} 条")
    except Exception as e:
        _sync_state["error"] = str(e)
        logger.warning(f"成人内容同步失败: {e}")
    finally:
        _sync_state["running"] = False
    return total


def sync_status() -> dict:
    return dict(_sync_state)
