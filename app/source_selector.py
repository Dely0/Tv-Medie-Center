"""多源测速优选：同一视频存在多个来源时，实测各源 CDN 速度与最高码率，
选出“带宽能支撑的最高码率”下的最快源。带结果缓存，避免每次播放重复测速。
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger("source_selector")

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "source_speed_cache.json")
_CACHE_TTL = 600  # 秒：CDN 测速结果 10 分钟内复用
_CANDIDATE_CACHE = {}  # 内存缓存：video_id -> (ts, candidates)
_CANDIDATE_TTL = 600
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _cache_read() -> dict:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_write(data: dict):
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _get_cache_key(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return url[:80]


def _cached_speed(url: str):
    cache = _cache_read()
    entry = cache.get(_get_cache_key(url))
    if entry and time.time() - entry.get("ts", 0) < _CACHE_TTL:
        return entry
    return None


def measure_source(url: str, referer: str = "", force: bool = False,
                   headers: dict = None, ua: str = None) -> dict:
    """测单个播放地址：解析 master 最高码率 + 下载首分片测速。
    返回 {play_url, host, speed_kbs, ttfb_ms, max_bandwidth_kbps, error}"""
    if not url or not url.startswith("http"):
        return {"play_url": url, "error": "无效地址"}
    cached = _cached_speed(url)
    if cached and not force:
        cached["play_url"] = url
        return cached

    h = {"User-Agent": ua or _UA}
    if headers:
        h.update({k: str(v) for k, v in headers.items() if k.lower() in (
            "user-agent", "referer", "origin", "cookie", "accept", "accept-language",
        )})
    if referer:
        h["Referer"] = referer
    result = {"play_url": url, "host": _get_cache_key(url)}
    try:
        # 1) 拉 master 清单，解析最高码率档
        t0 = time.time()
        r = requests.get(url, timeout=8, headers=h)
        if r.status_code != 200:
            result["error"] = f"master HTTP {r.status_code}"
            return result
        master = r.text
        max_bw = 0
        for m in re.finditer(r"#EXT-X-STREAM-INF:[^\n]*BANDWIDTH=(\d+)", master):
            max_bw = max(max_bw, int(m.group(1)))
        result["max_bandwidth_kbps"] = round(max_bw / 1000) if max_bw else None
        ttfb_master = (time.time() - t0) * 1000

        # 2) 取分片（master 或 media 清单的第一个片段）测速
        rels = [l for l in master.splitlines() if l and not l.startswith("#")]
        seg_url = ""
        if rels:
            rel = rels[0]
            if rel.startswith("/"):
                from urllib.parse import urljoin
                seg_url = urljoin(url, rel)
            elif rel.startswith("http"):
                seg_url = rel
            else:
                seg_url = url.rsplit("/", 1)[0] + "/" + rel
            # media 清单再深入一层取 ts
            if ".m3u8" in seg_url:
                try:
                    r2 = requests.get(seg_url, timeout=8, headers=h)
                    rels2 = [l for l in r2.text.splitlines() if l and not l.startswith("#")]
                    if rels2:
                        rl = rels2[0]
                        if rl.startswith("/"):
                            seg_url = urljoin(seg_url, rl)
                        elif rl.startswith("http"):
                            seg_url = rl
                        else:
                            seg_url = seg_url.rsplit("/", 1)[0] + "/" + rl
                except Exception:
                    pass

        if not seg_url or ".m3u8" in seg_url:
            result["error"] = "无法定位分片"
            return result

        t1 = time.time()
        rr = requests.get(seg_url, timeout=8, headers=h, stream=True)
        got = 0
        hard_deadline = time.time() + 9
        for chunk in rr.iter_content(65536):
            got += len(chunk)
            if got >= 524288:
                break
            if time.time() > hard_deadline:
                break
        t2 = time.time()
        dt = max(t2 - t1, 0.001)
        result["speed_kbs"] = round(got / dt / 1024)
        result["ttfb_ms"] = round(max(ttfb_master, (t1 - t0) * 1000))
        result["bytes"] = got
        result["ts"] = time.time()
        # 写缓存（按 host）
        cache = _cache_read()
        cache[_get_cache_key(url)] = {k: result[k] for k in
                                      ("speed_kbs", "ttfb_ms", "max_bandwidth_kbps", "ts")}
        _cache_write(cache)
    except Exception as e:
        result["error"] = str(e)[:80]
    return result


def find_best_source(video_id: int, episode: int = None, max_candidates: int = 5) -> dict:
    """查找同一视频在各源的最佳播放地址（本地库同标题记录）。
    返回 {"best": {...}, "alternatives": [...]}，按速度降序。"""
    from app.database import get_db, get_video_detail
    from app.adult import known_source_names
    from app.douban import normalize_title

    detail = get_video_detail(video_id)
    if not detail:
        return {"best": None, "alternatives": []}

    title = (detail.get("title") or "").strip()
    norm = normalize_title(title)
    if not norm:
        return {"best": None, "alternatives": []}

    exclude = set(known_source_names())
    current_id = detail["id"]
    current_source = detail.get("source", "")
    eps = detail.get("episodes") or []

    cache_key = f"{video_id}:{episode or 0}"
    cached = _CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _CANDIDATE_TTL:
        candidates = cached[1]
    else:
        candidates = []

        def pick_play_url(episodes, ep_num):
            if not episodes:
                return None, ""
            if ep_num:
                e = next((x for x in episodes if x.get("episode_num") == ep_num), None)
            else:
                e = episodes[0]
            if not e:
                e = episodes[0]
            return (e.get("play_url") or None), (e.get("episode_title") or f"第{e.get('episode_num')}集")

        # 当前记录
        cur_url, cur_ep_title = pick_play_url(eps, episode)
        if cur_url:
            candidates.append({"video_id": current_id, "source": current_source, "play_url": cur_url,
                               "episode_title": cur_ep_title, "current": True})

        # 本地库同标题的其他源记录
        with get_db() as db:
            rows = db.execute("SELECT id, title, source FROM videos WHERE title != ''").fetchall()
        for r in rows:
            if len(candidates) >= max_candidates:
                break
            if r["id"] == current_id:
                continue
            if r["source"] in exclude:
                continue
            if normalize_title(r["title"]) != norm:
                continue
            with get_db() as db2:
                eps2 = db2.execute(
                    "SELECT * FROM episodes WHERE video_id=? ORDER BY episode_num ASC",
                    (r["id"],)
                ).fetchall()
            u, ep_title = pick_play_url([dict(e) for e in eps2], episode)
            if u:
                candidates.append({"video_id": r["id"], "source": r["source"], "play_url": u,
                                   "episode_title": ep_title, "current": False})

        _CANDIDATE_CACHE[cache_key] = (time.time(), candidates)

        # 本地候选不足时，后台补充源站候选（不阻塞本次播放；结果下次调用生效）
        if len(candidates) < max_candidates:
            threading.Thread(
                target=_supplement_candidates,
                args=(cache_key, title, norm, episode, exclude, current_source, max_candidates),
                daemon=True,
            ).start()

    # 并发测速
    measured = []
    with ThreadPoolExecutor(max_workers=min(len(candidates), 4)) as pool:
        futures = {pool.submit(measure_source, c["play_url"], ""): c for c in candidates}
        for f in as_completed(futures):
            c = futures[f]
            try:
                m = f.result()
            except Exception:
                m = {"play_url": c["play_url"], "error": "测速异常"}
            m.update({"source": c["source"], "episode_title": c.get("episode_title", ""),
                      "current": c.get("current", False)})
            measured.append(m)

    # 排序：有速度的按速度降序，测速失败的排最后
    measured.sort(key=lambda x: (-(x.get("speed_kbs") or -1), 0 if x.get("current") else 1))
    best = measured[0] if measured and measured[0].get("speed_kbs") else (measured[0] if measured else None)
    return {"best": best, "alternatives": measured}


def _supplement_candidates(cache_key, title, norm, episode, exclude, current_source, max_candidates):
    """后台补充：源站搜索同标题视频 → 解析剧集 → 加入候选缓存。"""
    from app.source_framework.registry import get_search_sources
    cached = _CANDIDATE_CACHE.get(cache_key)
    candidates = list(cached[1]) if cached else []

    def pick_play_url(episodes, ep_num):
        if not episodes:
            return None, ""
        if ep_num:
            e = next((x for x in episodes if x.get("episode_num") == ep_num), None)
        else:
            e = episodes[0]
        if not e:
            e = episodes[0]
        return (e.get("play_url") or None), (e.get("episode_title") or f"第{e.get('episode_num')}集")

    for src in get_search_sources()[:3]:
        if len(candidates) >= max_candidates:
            break
        if src.name in exclude or src.name == current_source:
            continue
        if any(c.get("source") == src.name for c in candidates):
            continue
        try:
            items = src.search(title, timeout=5)
        except Exception:
            continue
        match = None
        for it in items:
            if normalize_title(it.get("title") or "") == norm:
                match = it
                break
        if not match:
            continue
        try:
            info, episodes2 = src.get_detail(match.get("source_url", ""))
        except Exception:
            continue
        u, ep_title = pick_play_url(episodes2, episode)
        if u:
            candidates.append({"video_id": None, "source": src.name,
                               "play_url": u, "episode_title": ep_title,
                               "current": False})
    _CANDIDATE_CACHE[cache_key] = (time.time(), candidates)
