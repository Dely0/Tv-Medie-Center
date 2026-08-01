"""源健康检查与自动隔离。

状态机：
  untested -> ok / slow / dead
  ok/slow  : 连续 SOURCE_HEALTH_DEAD_AFTER 次失败 -> dead（自动隔离）
  dead     : 任一次检查通过 -> ok（自动恢复）
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config as cfg

logger = logging.getLogger("ops_health")

_LOCK = threading.Lock()
_STATUS = None  # 内存缓存，避免频繁读盘


def _read() -> dict:
    global _STATUS
    if _STATUS is not None:
        return _STATUS
    try:
        with open(cfg.SOURCE_HEALTH_FILE, "r", encoding="utf-8") as f:
            _STATUS = json.load(f)
    except Exception:
        _STATUS = {}
    return _STATUS


def _write(data: dict):
    global _STATUS
    _STATUS = data
    try:
        with open(cfg.SOURCE_HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"健康状态写入失败: {e}")


def _record(name: str, ok: bool, latency_ms: float = None, error: str = ""):
    with _LOCK:
        data = _read()
        entry = data.get(name, {
            "state": "untested", "fails": 0, "oks": 0,
            "latency_ms": None, "checked_at": None, "last_error": "",
        })
        if ok:
            entry["fails"] = 0
            entry["oks"] = entry.get("oks", 0) + 1
            entry["state"] = "slow" if (latency_ms or 0) > 3000 else "ok"
            entry["last_error"] = ""
        else:
            entry["fails"] = entry.get("fails", 0) + 1
            entry["state"] = "dead" if entry["fails"] >= cfg.SOURCE_HEALTH_DEAD_AFTER else "slow"
            if error:
                entry["last_error"] = str(error)[:120]
        if latency_ms is not None:
            entry["latency_ms"] = round(latency_ms)
        entry["checked_at"] = time.time()
        data[name] = entry
        _write(data)


def _check_one(src) -> tuple[str, bool, float, str]:
    """检查单个源。src 需具备 _request 或 get_list_page 接口。"""
    name = getattr(src, "name", "unknown")
    result = []

    def _run():
        try:
            result.append(_check_inner(src))
        except Exception as e:
            result.append((name, False, 0, str(e)))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=10)
    if not result:
        return name, False, 10000, "检查超时"
    return result[0]


def _check_inner(src):
    name = getattr(src, "name", "unknown")
    t0 = time.time()
    if hasattr(src, "_request"):
        data = src._request({"ac": "list"}, timeout=8)
        # drpy 源首页可能 list 为空但 class（分类）正常，也算存活
        ok = bool(data and (data.get("list") or data.get("class")))
    else:
        items = src.get_list_page("movie", 1, 5)
        ok = bool(items)
    latency = (time.time() - t0) * 1000
    error = "" if ok else "无数据返回"
    return name, ok, latency, error


def run_health_check(sources=None, force: bool = True) -> dict:
    """并行检查所有源，更新状态文件。返回摘要。"""
    if sources is not None:
        src_list = list(sources)
    else:
        from app.source_framework.registry import get_drpy_enabled_sources
        from app.maccms_source import get_maccms_crawlable_sources
        src_list = get_maccms_crawlable_sources() + get_drpy_enabled_sources()
    if not src_list:
        return {"checked": 0, "ok": 0, "dead": 0, "sources": []}

    summary = {"checked": 0, "ok": 0, "dead": 0, "sources": []}
    pool = ThreadPoolExecutor(max_workers=min(len(src_list), 4))
    futures = [pool.submit(_check_one, s) for s in src_list]
    try:
        for f in as_completed(futures, timeout=150):
            try:
                name, ok, latency, error = f.result(timeout=2)
            except Exception:
                continue
            _record(name, ok, latency, error)
            summary["checked"] += 1
            if ok:
                summary["ok"] += 1
            entry = _read().get(name, {})
            if entry.get("state") == "dead":
                summary["dead"] += 1
            summary["sources"].append({
                "name": name,
                "state": entry.get("state", "untested"),
                "latency_ms": entry.get("latency_ms"),
                "fails": entry.get("fails", 0),
                "checked_at": entry.get("checked_at"),
                "last_error": entry.get("last_error", ""),
            })
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False)
    logger.info(f"健康检查完成: 检查 {summary['checked']}, 正常 {summary['ok']}, 隔离 {summary['dead']}")
    return summary


def is_source_dead(name: str) -> bool:
    return _read().get(name, {}).get("state") == "dead"


def get_status() -> dict:
    data = _read()
    return {
        "checked_at": max([e.get("checked_at", 0) for e in data.values()] or [0]),
        "sources": [
            {"name": k, **{kk: vv for kk, vv in v.items()}}
            for k, v in data.items()
        ],
    }


def start_health_scheduler():
    """后台健康检查调度：启动即检查一次，之后每 SOURCE_HEALTH_INTERVAL 秒检查。"""
    def loop():
        try:
            run_health_check()
        except Exception as e:
            logger.warning(f"启动健康检查失败: {e}")
        while True:
            time.sleep(cfg.SOURCE_HEALTH_INTERVAL)
            try:
                run_health_check()
            except Exception as e:
                logger.warning(f"周期健康检查失败: {e}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    logger.info(f"源健康检查调度器已启动（间隔 {cfg.SOURCE_HEALTH_INTERVAL // 3600} 小时）")
