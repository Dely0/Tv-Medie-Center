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


def _measure_source_speed(src):
    """深度健康检查：取该源电影分类第一条，解析真实播放地址并实测 CDN 缓冲速度。"""
    name = getattr(src, "name", "unknown")
    try:
        if hasattr(src, "list_page"):
            items = src.list_page("movie", 1, 8)
        else:
            items = src.get_list_page("movie", 1, 8)
        sample_items = [it for it in items if it.get("source_url")][:3]
        if not sample_items:
            return name, None
        from app.source_selector import measure_source
        profile = getattr(src, "header_profile", None) or {}
        best = 0
        for item in sample_items:
            candidates = []
            try:
                candidates = src.get_play_candidates(item.get("source_url", ""), 5)
            except Exception:
                try:
                    u = src.get_play_url(item.get("source_url", ""))
                    if u and str(u).startswith(("http://", "https://")):
                        candidates = [str(u)]
                except Exception:
                    pass
            if not candidates:
                continue
            with ThreadPoolExecutor(max_workers=min(len(candidates), 4)) as pool:
                futs = {pool.submit(measure_source, u, profile.get("referer", ""),
                                    False, profile or {}, profile.get("ua")): u
                        for u in candidates}
                try:
                    for f in as_completed(futs, timeout=12):
                        try:
                            m = f.result(timeout=1)
                        except Exception:
                            continue
                        sp = m.get("speed_kbs")
                        if sp and sp > best:
                            best = sp
                except Exception:
                    pass
                finally:
                    pool.shutdown(wait=False)
        return name, best or None
    except Exception:
        return name, None


def _update_speed(name: str, speed_kbs):
    with _LOCK:
        data = _read()
        entry = data.setdefault(name, {
            "state": "untested", "fails": 0, "oks": 0,
            "latency_ms": None, "checked_at": None, "last_error": "",
        })
        entry["speed_kbs"] = speed_kbs
        entry["priority"] = int(speed_kbs or 0)
        entry["speed_at"] = time.time()
        _write(data)


def record_source_speed(name: str, speed_kbs):
    """把真实播放/测速结果回写为滚动均值，与深度探测取最大值作为优先级。
    这样"实际用起来快"的源优先级会自动上涨。"""
    if not speed_kbs or speed_kbs <= 0:
        return
    try:
        speed_kbs = int(speed_kbs)
    except Exception:
        return
    with _LOCK:
        data = _read()
        entry = data.setdefault(name, {
            "state": "untested", "fails": 0, "oks": 0,
            "latency_ms": None, "checked_at": None, "last_error": "",
        })
        old_ema = entry.get("speed_ema")
        if old_ema is None:
            entry["speed_ema"] = speed_kbs
        else:
            entry["speed_ema"] = round(int(old_ema) * 0.7 + speed_kbs * 0.3)
        entry["speed_samples"] = int(entry.get("speed_samples") or 0) + 1
        entry["priority"] = max(int(entry.get("speed_kbs") or 0), int(entry["speed_ema"]))
        entry["speed_at"] = time.time()
        _write(data)


def source_priority(name: str) -> int:
    """源优先级：主要由实测 CDN 缓冲速度决定（速度越快优先级越高）。"""
    return int(_read().get(name, {}).get("priority") or 0)


def sorted_by_priority(sources: list) -> list:
    """按优先级排序（高优先在前；同优先级按延迟，未测的最后）。"""
    data = _read()
    def key(s):
        h = data.get(getattr(s, "name", ""), {})
        return (-int(h.get("priority") or 0),
                h.get("latency_ms") if h.get("latency_ms") is not None else 999999,
                getattr(s, "name", ""))
    return sorted(sources, key=key)


def run_health_check(sources=None, force: bool = True, deep: bool = True) -> dict:
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
    # 深度检查：实测每个源的 CDN 缓冲速度并更新优先级（后台，慢源不阻塞）
    if deep and src_list:
        speed_pool = ThreadPoolExecutor(max_workers=6)
        speed_futs = [speed_pool.submit(_measure_source_speed, s) for s in src_list]
        try:
            for f in as_completed(speed_futs, timeout=240):
                try:
                    name, speed = f.result(timeout=1)
                except Exception:
                    continue
                if speed:
                    _update_speed(name, speed)
                    summary["sources"].append({"name": name, "speed_kbs": speed})
        except Exception:
            pass
        finally:
            speed_pool.shutdown(wait=False)
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
