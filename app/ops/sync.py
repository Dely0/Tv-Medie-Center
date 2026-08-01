"""社区 TVBox 配置订阅同步：多地址拉取 -> 解析 -> 合并 MacCMS 源与解析器 -> 热加载。"""
import json
import logging
import os
import threading
import time

import config as cfg

logger = logging.getLogger("ops_sync")


def _sync_once() -> dict:
    from app.source_framework.tvbox_config import (
        fetch_tvbox_config,
        extract_maccms_entries,
        extract_parse_entries,
        merge_community_sources,
        save_parse_sources,
    )
    from app.maccms_source import get_manager

    result = {"success": 0, "failed": [], "added": 0, "total": 0, "parses": 0}
    entries = []
    parse_entries = []
    for url in cfg.TVBOX_SUBSCRIPTIONS:
        try:
            data = fetch_tvbox_config(url, timeout=20)
            if not data or not isinstance(data, dict):
                result["failed"].append(url)
                continue
            entries.extend(extract_maccms_entries(data.get("sites", []), source_url=url))
            parse_entries.extend(extract_parse_entries(data.get("parses", [])))
            result["success"] += 1
        except Exception as e:
            logger.warning(f"订阅拉取失败 {url}: {e}")
            result["failed"].append(url)

    added, total = merge_community_sources(entries)
    result["added"] = added
    result["total"] = total
    result["parses"] = save_parse_sources(parse_entries)

    if os.path.exists(cfg.COMMUNITY_SOURCES_FILE):
        primary = os.path.join(os.path.dirname(__file__), "..", "..", "data", "maccms_sources.json")
        get_manager().load_from_config(primary)

    # 刷新 drpyS 源注册表（拉取最新 /config/1）
    try:
        from app.source_framework.drpy_source import refresh_registry
        result["drpy_refresh"] = refresh_registry(force=True)
    except Exception as e:
        logger.warning(f"drpyS 注册表刷新失败: {e}")
        result["drpy_refresh"] = False

    return result


def sync_now() -> dict:
    """立即同步（供 API 调用，后台执行避免阻塞）。"""
    result = {}

    def run():
        nonlocal result
        try:
            result = _sync_once()
        except Exception as e:
            result = {"error": str(e)}

    threading.Thread(target=run, daemon=True).start()
    return {"success": True, "message": "社区源同步已在后台启动"}


def start_sync_scheduler():
    """每日同步社区源配置。"""
    def loop():
        try:
            _sync_once()
        except Exception as e:
            logger.warning(f"启动社区源同步失败: {e}")
        while True:
            time.sleep(cfg.TVBOX_SYNC_INTERVAL)
            try:
                _sync_once()
            except Exception as e:
                logger.warning(f"周期社区源同步失败: {e}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    logger.info(f"社区源订阅调度器已启动（间隔 {cfg.TVBOX_SYNC_INTERVAL // 3600} 小时）")
