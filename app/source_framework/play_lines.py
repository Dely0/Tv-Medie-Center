"""播放链：同一视频在所有启用源中的候选线路，测速排序，供前端无感换源。"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

import config as cfg
from app.database import get_db, get_video_detail
from app.douban import normalize_title
from app.net.headers import needs_proxy
from app.source_framework import parse as parse_mod

logger = logging.getLogger("play_lines")

_LINES_CACHE = {}
_LINES_LOCK = threading.Lock()
_LINES_TTL = 600


def _pick_episode(episodes, episode):
    if not episodes:
        return None, ""
    ep = None
    if episode:
        ep = next((e for e in episodes if e.get("episode_num") == episode), None)
    if not ep:
        ep = episodes[0]
    return ep, (ep.get("episode_title") or f"第{ep.get('episode_num')}集")


def _header_profile(source_name: str) -> dict | None:
    from app.maccms_source import get_manager
    src = get_manager().get_by_name(source_name)
    return getattr(src, "header_profile", None) if src else None


def _gather_candidates(video_id: int, episode: int | None, max_candidates: int):
    """收集候选线路（本地库同标题 + 当前视频）。"""
    from app.adult import known_source_names
    from app.ops.health import is_source_dead

    detail = get_video_detail(video_id)
    if not detail:
        return []
    title = (detail.get("title") or "").strip()
    norm = normalize_title(title)
    if not norm:
        return []
    exclude = set(known_source_names())
    current_source = detail.get("source", "")
    eps = detail.get("episodes") or []
    candidates = []

    cur_ep, cur_title = _pick_episode(eps, episode)
    if cur_ep and cur_ep.get("play_url"):
        candidates.append({
            "video_id": detail["id"],
            "source": current_source,
            "play_url": cur_ep["play_url"],
            "episode_title": cur_title,
            "current": True,
        })

    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, source FROM videos WHERE title != '' ORDER BY updated_at DESC"
        ).fetchall()
    for r in rows:
        if len(candidates) >= max_candidates:
            break
        if r["id"] == detail["id"]:
            continue
        if r["source"] in exclude or r["source"] == current_source:
            continue
        if is_source_dead(r["source"]):
            continue
        if normalize_title(r["title"]) != norm:
            continue
        with get_db() as db2:
            eps2 = db2.execute(
                "SELECT * FROM episodes WHERE video_id=? ORDER BY episode_num ASC",
                (r["id"],)
            ).fetchall()
        ep, ep_title = _pick_episode([dict(e) for e in eps2], episode)
        if ep and ep.get("play_url"):
            candidates.append({
                "video_id": r["id"],
                "source": r["source"],
                "play_url": ep["play_url"],
                "episode_title": ep_title,
                "current": False,
            })
    return candidates


def _measure_candidates(candidates: list[dict], timeout: float) -> list[dict]:
    """并行测速（带缓存），返回增强后的线路列表。"""
    from app.source_selector import measure_source
    lines = []
    pool = ThreadPoolExecutor(max_workers=min(len(candidates), 4))
    futures = {}
    for c in candidates:
        profile = _header_profile(c["source"])
        futures[pool.submit(
            measure_source, c["play_url"],
            (profile or {}).get("referer", ""),
            False,
        )] = (c, profile)
    deadline = time.time() + timeout
    done = set()
    try:
        for f in as_completed(futures, timeout=timeout + 1):
            done.add(f)
            c, profile = futures[f]
            try:
                m = f.result(timeout=max(0.1, deadline - time.time()))
            except Exception:
                m = {"play_url": c["play_url"], "error": "测速异常"}
            lines.append(_build_line(c, profile, m))
    except FuturesTimeout:
        # 超时：未完成的候选标记为测速超时，避免整个播放链失败
        for f, (c, profile) in futures.items():
            if f in done:
                continue
            f.cancel()
            lines.append(_build_line(c, profile, {
                "play_url": c["play_url"], "error": "测速超时",
            }))
    finally:
        pool.shutdown(wait=False)
    lines.sort(key=lambda x: (-(x.get("speed_kbs") or -1), 0 if x.get("current") else 1))
    return lines


def _build_line(c: dict, profile: dict | None, m: dict) -> dict:
    return {
        "source": c["source"],
        "play_url": m.get("play_url") or c["play_url"],
        "episode_title": c.get("episode_title", ""),
        "current": c.get("current", False),
        "kind": "direct",
        "speed_kbs": m.get("speed_kbs"),
        "ttfb_ms": m.get("ttfb_ms"),
        "max_bandwidth_kbps": m.get("max_bandwidth_kbps"),
        "error": m.get("error"),
        "headers": profile or {},
        "use_proxy": needs_proxy(profile),
    }


def _background_resolve(cache_key: str, candidates: list[dict]):
    """后台：对非直链候选做播放页嗅探，更新缓存。"""
    try:
        with _LINES_LOCK:
            cached = _LINES_CACHE.get(cache_key)
        if not cached:
            return
        lines = cached[1]
        changed = False
        for i, line in enumerate(lines):
            url = line.get("play_url", "")
            if not url.startswith("http") or line.get("kind") != "direct":
                continue
            if any(url.lower().endswith(ext) for ext in (".m3u8", ".mp4", ".flv", ".ts", ".mkv")):
                continue
            resolved = parse_mod.resolve_play_page(url, line.get("headers"))
            if resolved and resolved != url:
                lines[i]["play_url"] = resolved
                lines[i]["kind"] = "parse"
                changed = True
        if changed:
            with _LINES_LOCK:
                _LINES_CACHE[cache_key] = (time.time(), lines)
    except Exception as e:
        logger.debug(f"后台解析失败: {e}")


def get_play_lines(video_id: int, episode: int | None = None, refresh: bool = False) -> dict:
    """获取播放链：本地候选 -> 测速排序 -> 后台嗅探补充。"""
    cache_key = f"{video_id}:{episode or 0}"
    with _LINES_LOCK:
        cached = _LINES_CACHE.get(cache_key)
        if cached and not refresh and time.time() - cached[0] < _LINES_TTL:
            return {"lines": cached[1], "cached": True}

    candidates = _gather_candidates(video_id, episode, cfg.PLAY_LINES_LIMIT)
    if not candidates:
        return {"lines": [], "cached": False}
    lines = _measure_candidates(candidates, cfg.PLAY_LINES_MEASURE_TIMEOUT)
    with _LINES_LOCK:
        _LINES_CACHE[cache_key] = (time.time(), lines)

    # 后台嗅探非直链候选（不阻塞本次返回）
    need_resolve = any(
        l.get("play_url") and not l["play_url"].lower().endswith(
            (".m3u8", ".mp4", ".flv", ".ts", ".mkv")
        ) for l in lines
    )
    if need_resolve:
        threading.Thread(
            target=_background_resolve, args=(cache_key, candidates), daemon=True
        ).start()

    return {"lines": lines, "cached": False}
