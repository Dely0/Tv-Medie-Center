"""社区 TVBox 配置订阅同步：多地址拉取 -> 解析 -> 合并 MacCMS 源与解析器 -> 热加载。"""
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import config as cfg

logger = logging.getLogger("ops_sync")


def merge_drpy_collect_sources(test_working: bool = True) -> dict:
    """把 drpyS 自带的采集源清单（采集2024/2025/2026静态.json 等）并入社区 MacCMS 源池。
    - 跳过 [密] 成人清单
    - 按 base_url 去重、按名称去重（重名自动加序号）
    - 可选：并行测通（ac=list 有数据）才入库
    """
    from app.source_framework.tvbox_config import merge_community_sources
    from app.maccms_source import get_manager

    _ADULT_NAME_HINTS = ("AV", "成人", "番号", "老色逼", "湿乐园", "奶香香", "色")

    # 全局隔离：跳过已知成人源（按名称与 base_url 双重匹配，避免“滴滴/滴滴资源/♥155(直连)”
    # 这类不带成人字样但内容全为成人视频的采集源被重新并回普通源池）
    from app.adult import known_source_names as _adult_known_names
    from app.adult import load_config as _adult_load_config
    _adult_names = set(_adult_known_names())
    _adult_bases = set()
    for _s in (_adult_load_config().get("sources") or []):
        _adult_names.add(_s.get("name") or "")
        _b = (_s.get("base_url") or "").rstrip("/")
        if _b:
            _adult_bases.add(_b)

    json_dir = os.path.join(cfg.DRPYS_DIR, "json")
    existing_bases = {s.base_url for s in get_manager().get_all()}
    existing_names = {s.name for s in get_manager().get_all()}
    entries = []
    seen_bases = set(existing_bases)
    seen_names = set(existing_names)
    try:
        files = [f for f in os.listdir(json_dir) if f.endswith("_json")]
    except Exception:
        files = []
    for f in files:
        if "\u5bc6" in f:
            continue  # 成人清单不并入
        try:
            with open(os.path.join(json_dir, f), encoding="utf-8-sig") as fp:
                data = json.load(fp)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            base = (it.get("url") or "").strip().rstrip("/")
            name = (it.get("name") or "").strip()
            if not base or base in seen_bases:
                continue
            if any(h in name for h in _ADULT_NAME_HINTS):
                continue
            if name in _adult_names or base in _adult_bases:
                continue
            if not name:
                name = base
            if name in seen_names:
                i = 2
                while f"{name}{i}" in seen_names:
                    i += 1
                name = f"{name}{i}"
            seen_bases.add(base)
            seen_names.add(name)
            entries.append({
                "name": name,
                "base_url": base,
                "enabled": True,
                "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
                "source_type": "maccms",
                "from_config": "drpys-collect",
            })

    if test_working and entries:
        def _test(e):
            url = e["base_url"] + "/api.php/provide/vod?" + urllib.parse.urlencode(
                {"ac": "list", "at": "json", "pagesize": "3"}
            )
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0", "Referer": e["base_url"],
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                return e, bool(data.get("code") == 1 and data.get("list"))
            except Exception:
                return e, False

        working = []
        pool = ThreadPoolExecutor(max_workers=12)
        futures = [pool.submit(_test, e) for e in entries]
        try:
            for f in as_completed(futures, timeout=90):
                try:
                    e, ok = f.result(timeout=1)
                except Exception:
                    continue
                if ok:
                    working.append(e)
        except Exception:
            pass
        finally:
            pool.shutdown(wait=False)
        entries = working

    added, total = merge_community_sources(entries)
    logger.info(f"drpy 采集源合并: 候选 {len(entries)}, 新增 {added}, 社区源总数 {total}")
    return {"candidates": len(entries), "added": added, "total": total}


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

    # 采集源清单并入社区 MacCMS 池（并行测通后入库）
    try:
        result["collect_merge"] = merge_drpy_collect_sources(test_working=True)
    except Exception as e:
        logger.warning(f"采集源合并失败: {e}")
        result["collect_merge"] = {"error": str(e)}

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
