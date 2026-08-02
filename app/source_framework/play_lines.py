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
_SUPPLEMENTING = set()
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
    if src:
        return getattr(src, "header_profile", None)
    from app.source_framework.drpy_source import get_registry
    dsrc = get_registry().get_by_name(source_name)
    return dsrc.header_profile if dsrc else None


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
    # 本地没存剧集时（如 drpy 回填只存了列表），实时从当前源解析
    if not cur_ep and detail.get("source") and detail.get("source_url"):
        try:
            from app.source_framework.registry import get_source_by_name
            src = get_source_by_name(detail.get("source", ""))
            if src:
                _, eps_remote = src.get_detail(detail.get("source_url", ""))
                cur_ep, cur_title = _pick_episode(eps_remote, episode)
        except Exception:
            pass
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
            profile or {},
            (profile or {}).get("ua"),
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
    from app.source_framework.drpy_source import get_registry
    is_drpy = get_registry().get_by_name(c["source"]) is not None
    url = m.get("play_url") or c["play_url"]
    is_media = _is_media_url(url)
    return {
        "source": c["source"],
        "play_url": url,
        "episode_title": c.get("episode_title", ""),
        "current": c.get("current", False),
        "kind": "direct" if is_media else "page",
        "speed_kbs": m.get("speed_kbs"),
        "ttfb_ms": m.get("ttfb_ms"),
        "max_bandwidth_kbps": m.get("max_bandwidth_kbps"),
        # HTML 播放页线路不能直接播，标记为待解析，切换时跳过
        "error": m.get("error") if is_media else (m.get("error") or "播放页待解析"),
        "headers": profile or {},
        # 带 Referer/UA 的线路（含全部 MacCMS 源）与 drpy 源统一走本地代理
        "use_proxy": needs_proxy(profile) or is_drpy,
    }


def _is_media_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    try:
        from urllib.parse import urlparse
        return urlparse(url).path.lower().endswith((".mp4", ".m3u8", ".flv", ".ts", ".mkv"))
    except Exception:
        return False


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
                lines[i]["error"] = None
                changed = True
        if changed:
            with _LINES_LOCK:
                _LINES_CACHE[cache_key] = (time.time(), lines)
    except Exception as e:
        logger.debug(f"后台解析失败: {e}")


def _background_supplement(cache_key: str, video_id: int, title: str, norm: str,
                           episode: int | None, current_source: str):
    """后台跨源补充：在所有启用源（含 drpy）中搜索同标题视频并解析剧集，
    把新线路测速后合并进播放链缓存。"""
    from app.source_framework.registry import get_search_sources
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        with _LINES_LOCK:
            cached = _LINES_CACHE.get(cache_key)
        lines = list(cached[1]) if cached else []
        have = {l.get("source") for l in lines}

        def find_match(src):
            try:
                items = src.search(title, timeout=6)
            except Exception:
                return None
            match = None
            for it in items:
                if normalize_title(it.get("title") or "") == norm:
                    match = it
                    break
            return (src, match) if match else None

        # 阶段一：并行搜索全部源，收集匹配
        sources = get_search_sources()
        pool = ThreadPoolExecutor(max_workers=min(len(sources), 6))
        futures = [pool.submit(find_match, s) for s in sources]
        matches = []
        try:
            for f in as_completed(futures, timeout=14):
                try:
                    r = f.result(timeout=1)
                except Exception:
                    continue
                if not r:
                    continue
                src, match = r
                if src.name in have or src.name == current_source:
                    continue
                matches.append((src, match))
                have.add(src.name)
                if len(matches) >= 8:
                    break
        except Exception:
            pass
        finally:
            pool.shutdown(wait=False)

        if not matches:
            return

        # 阶段二：并行解析匹配项的剧集
        additions = []
        detail_pool = ThreadPoolExecutor(max_workers=min(len(matches), 6))
        detail_futures = [detail_pool.submit(src.get_detail, match.get("source_url", ""))
                          for src, match in matches]
        try:
            for f in as_completed(detail_futures, timeout=16):
                try:
                    _, eps2 = f.result(timeout=1)
                except Exception:
                    continue
                if not eps2:
                    continue
                ep, ep_title = _pick_episode(eps2, episode)
                if ep and ep.get("play_url"):
                    src = matches[detail_futures.index(f)][0]
                    additions.append({
                        "video_id": None,
                        "source": src.name,
                        "play_url": ep["play_url"],
                        "episode_title": ep_title,
                        "current": False,
                    })
        except Exception:
            pass
        finally:
            detail_pool.shutdown(wait=False)

        if not additions:
            return
        measured = _measure_candidates(additions, min(6.0, cfg.PLAY_LINES_MEASURE_TIMEOUT))
        with _LINES_LOCK:
            cached = _LINES_CACHE.get(cache_key)
            lines = list(cached[1]) if cached else []
            existing_urls = {l.get("play_url") for l in lines}
            for m in measured:
                if m.get("play_url") and m["play_url"] not in existing_urls:
                    lines.append(m)
                    existing_urls.add(m["play_url"])
            lines.sort(key=lambda x: (-(x.get("speed_kbs") or -1), 0 if x.get("current") else 1))
            _LINES_CACHE[cache_key] = (time.time(), lines[:12])
        logger.info(f"播放链后台补充: {cache_key} 新增 {len(measured)} 条线路")
    except Exception as e:
        logger.debug(f"播放链后台补充失败: {e}")
    finally:
        _SUPPLEMENTING.discard(cache_key)


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

    # 无论本地候选多少，都后台跨全部源搜索补充（含 drpy 新源），不阻塞本次返回
    if cache_key not in _SUPPLEMENTING:
        _SUPPLEMENTING.add(cache_key)
        detail = get_video_detail(video_id)
        title = (detail or {}).get("title", "")
        norm = normalize_title(title)
        current_source = (detail or {}).get("source", "")
        if norm:
            threading.Thread(
                target=_background_supplement,
                args=(cache_key, video_id, title, norm, episode, current_source),
                daemon=True,
            ).start()

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
