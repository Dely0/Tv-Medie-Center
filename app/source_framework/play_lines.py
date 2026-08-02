"""播放链：同一视频在所有启用源中的候选线路，测速排序，供前端无感换源。"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

import config as cfg
from app.database import get_db, get_video_detail
from app.douban import normalize_title
from app.net.headers import needs_proxy
from app.source_framework import parse as parse_mod

logger = logging.getLogger("play_lines")

_COMMON_SUFFIX_RE = re.compile(
    r"(国语|粤语|普通话|中字|双语|高清|完整版|未删减|加长版|蓝光|超清)$"
)

_LINES_CACHE = {}
_LINES_LOCK = threading.Lock()
_SUPPLEMENTING = set()            # active supplement cache keys (global single session)
_SUPPLEMENT_QUEUE = []            # FIFO waiting cache keys
_SUPPLEMENT_SESSION_START = {}    # cache_key -> session start time
_SUPPLEMENT_SESSION_TTL = 600     # max seconds per video supplement session (10 min)
_SUPPLEMENT_ROUND_SOURCES = 6     # sources searched per round
_SEARCHED = {}                    # cache_key -> {source_name: last searched time}
_LAST_REQUEST = {}                # cache_key -> last get_play_lines time (session liveness)
_MAX_CONCURRENT_SUPPLEMENTS = 2   # concurrent supplement sessions (miniPC friendly)
_SUPPLEMENT_STALE_AFTER = 60      # stop a session 60s after its video stopped being polled
_SUPPLEMENT_MIN_RUN = 30          # never stop a session younger than 30s
_SEARCHED_TTL = 1800              # searched-source cooldown (30 min)
_LINES_TTL = 600


def _store_cache_locked(cache_key: str, lines: list, ts: float | None = None) -> int:
    """Store play-lines cache and bump revision. Returns the new revision."""
    cached = _LINES_CACHE.get(cache_key)
    revision = (cached[2] if cached else 0) + 1
    _LINES_CACHE[cache_key] = (ts if ts is not None else time.time(), lines, revision)
    return revision


def _pick_episode(episodes, episode):
    if not episodes:
        return None, ""
    ep = None
    if episode:
        ep = next((e for e in episodes if e.get("episode_num") == episode), None)
    if not ep:
        ep = episodes[0]
    return ep, (ep.get("episode_title") or f"第{ep.get('episode_num')}集")


def _title_variants(title: str, source: str = "") -> list[str]:
    """生成用于跨源匹配的标题变体。
    alist-tvbox 的标题是文件名（如 [阿凡达].Avatar.2009.mkv），
    需要额外清洗出“阿凡达”才能与常规源的标题精确匹配。
    """
    variants = [normalize_title(title)]
    if source == "alist-tvbox":
        try:
            from app.source_framework.alist_tvbox_adapter import clean_file_title
            v = normalize_title(clean_file_title(title))
        except Exception:
            v = ""
        if v and v not in variants:
            variants.append(v)
    return [v for v in variants if v]


def _title_match(variant: str, other: str) -> bool:
    """严格标题匹配：相等，或“主标题+常见后缀”（如 阿凡达普通话版）。"""
    if not variant or not other:
        return False
    if variant == other:
        return True
    if len(variant) >= 3 and other.startswith(variant):
        suffix = other[len(variant):]
        return bool(suffix and _COMMON_SUFFIX_RE.fullmatch(suffix))
    return False


def _header_profile(source_name: str) -> dict | None:
    from app.maccms_source import get_manager
    src = get_manager().get_by_name(source_name)
    if src:
        return getattr(src, "header_profile", None)
    from app.source_framework.drpy_source import get_registry
    dsrc = get_registry().get_by_name(source_name)
    return dsrc.header_profile if dsrc else None


def _pick_fastest_play_url(src, source_url: str, episode: int | None = None,
                           timeout: float = 8.0):
    """取该视频指定集的所有线路地址（含请求头），并行实测后返回（最快URL, 速度, 请求头）。"""
    try:
        candidates = src.resolve_play_lines(source_url, episode, 4)
    except Exception:
        candidates = []
    if not candidates:
        # 详情接口偶发超时：重试一次
        try:
            candidates = src.resolve_play_lines(source_url, episode, 4)
        except Exception:
            candidates = []
    if not candidates:
        return None, None, None
    from app.source_selector import measure_source
    best_url, best_speed = candidates[0]["url"], 0
    best_header = candidates[0].get("header") or {}
    pool = ThreadPoolExecutor(max_workers=min(len(candidates), 4))
    futs = {}
    for cand in candidates:
        h = cand.get("header") or {}
        futs[pool.submit(measure_source, cand["url"], h.get("referer", ""),
                         False, h, h.get("ua"))] = cand
    try:
        for f in as_completed(futs, timeout=timeout + 2):
            try:
                m = f.result(timeout=1)
            except Exception:
                continue
            sp = m.get("speed_kbs")
            if sp and sp > best_speed:
                best_speed = sp
                best_url = futs[f]["url"]
                best_header = futs[f].get("header") or {}
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False)
    if best_speed:
        try:
            from app.ops.health import record_source_speed
            record_source_speed(getattr(src, "name", ""), best_speed)
        except Exception:
            pass
    return best_url, best_speed or None, best_header


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
    variants = _title_variants(title, detail.get("source", ""))
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
        nlocal = normalize_title(r["title"])
        if not any(_title_match(v, nlocal) for v in variants):
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
        profile = c.get("headers") or _header_profile(c["source"])
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
    try:
        if m.get("speed_kbs"):
            from app.ops.health import record_source_speed
            record_source_speed(c["source"], m["speed_kbs"])
    except Exception:
        pass
    profile = c.get("headers") or profile
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
        p = urlparse(url)
        if p.path.lower().endswith((".mp4", ".m3u8", ".flv", ".ts", ".mkv")):
            return True
        # alist-tvbox 本地代理 /p/{token}/{id}：无扩展名但为直接媒体流
        host = (p.hostname or "")
        if host in ("127.0.0.1", "localhost") and p.port == 4567 and p.path.startswith("/p/"):
            return True
        return False
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
                _store_cache_locked(cache_key, lines)
    except Exception as e:
        logger.debug(f"后台解析失败: {e}")


def _schedule_supplement(cache_key, video_id, title, norm, episode, current_source, current_source_url=""):
    """Start a supplement session immediately when a slot is free, otherwise queue it."""
    with _LINES_LOCK:
        if cache_key in _SUPPLEMENTING or cache_key in _SUPPLEMENT_QUEUE:
            return
        if _supplement_done_locked(cache_key):
            return
        if len(_SUPPLEMENTING) < _MAX_CONCURRENT_SUPPLEMENTS:
            _SUPPLEMENTING.add(cache_key)
            _SUPPLEMENT_SESSION_START[cache_key] = time.time()
            threading.Thread(
                target=_supplement_session,
                args=(cache_key, video_id, title, norm, episode, current_source, current_source_url),
                daemon=True,
            ).start()
            return
        _SUPPLEMENT_QUEUE.append((cache_key, video_id, title, norm, episode, current_source, current_source_url))


def _supplement_done_locked(cache_key: str) -> bool:
    """True when the video session is expired or every enabled source was searched recently."""
    start = _SUPPLEMENT_SESSION_START.get(cache_key)
    if start and time.time() - start >= _SUPPLEMENT_SESSION_TTL:
        return True
    searched = _SEARCHED.get(cache_key, {})
    if not searched:
        return False
    try:
        from app.source_framework.registry import get_search_sources
        all_sources = get_search_sources(include_dead=True)
    except Exception:
        return True
    now = time.time()
    return all(
        s.name in searched and (now - searched[s.name]) < _SEARCHED_TTL
        for s in all_sources
    )


def _start_next_supplement_locked():
    """Start queued supplement sessions while slots are free (caller must hold _LINES_LOCK)."""
    while _SUPPLEMENT_QUEUE and len(_SUPPLEMENTING) < _MAX_CONCURRENT_SUPPLEMENTS:
        key, vid, title, norm, ep, cur_src, cur_url = _SUPPLEMENT_QUEUE.pop(0)
        if key in _SUPPLEMENTING:
            continue
        if _supplement_done_locked(key):
            continue
        _SUPPLEMENTING.add(key)
        _SUPPLEMENT_SESSION_START[key] = time.time()
        threading.Thread(
            target=_supplement_session,
            args=(key, vid, title, norm, ep, cur_src, cur_url),
            daemon=True,
        ).start()
        return


def _next_sources_locked(cache_key: str, limit: int):
    """Pick up to `limit` enabled sources not yet searched and not already in lines."""
    from app.source_framework.registry import get_search_sources
    from app.ops.health import sorted_by_priority
    now = time.time()
    searched = {
        name for name, ts in _SEARCHED.get(cache_key, {}).items()
        if now - ts < _SEARCHED_TTL
    }
    cached = _LINES_CACHE.get(cache_key)
    have = {l.get("source") for l in cached[1]} if cached else set()
    sources = [
        s for s in sorted_by_priority(get_search_sources(include_dead=True))
        if s.name not in searched and s.name not in have
    ]
    return sources[:limit]


def _search_round(sources, title, norm, source=""):
    """Round phase 1: search sources in parallel, return title matches (max 6)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    matches = []
    if not sources:
        return matches
    variants = _title_variants(title, source)
    # 对 alist 文件名条目，优先用清洗后的主标题搜索（源站不认识文件名）
    search_title = variants[-1] if variants else title

    def find_match(src):
        try:
            items = src.search(search_title, timeout=4)
        except Exception:
            return None
        for it in items:
            it_norm = normalize_title(it.get("title") or "")
            if any(_title_match(v, it_norm) for v in variants):
                return (src, it)
        return None

    pool = ThreadPoolExecutor(max_workers=min(len(sources), 6))
    futures = [pool.submit(find_match, s) for s in sources]
    try:
        for f in as_completed(futures, timeout=20):
            try:
                r = f.result(timeout=1)
            except Exception:
                continue
            if not r:
                continue
            matches.append(r)
            if len(matches) >= 6:
                break
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False)
    return matches


def _resolve_matches(matches, episode):
    """Round phase 2: resolve fastest play url per match, persist, return additions."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    additions = []
    if not matches:
        return additions
    pool = ThreadPoolExecutor(max_workers=min(len(matches), 6))
    futures = {
        pool.submit(_pick_fastest_play_url, src, match.get("source_url", ""), episode): (src, match)
        for src, match in matches
    }
    try:
        for f in as_completed(futures, timeout=30):
            try:
                fast_url, fast_speed, fast_header = f.result(timeout=1)
            except Exception:
                continue
            src, match = futures[f]
            logger.info(
                f"play-lines resolve item: {src.name} "
                f"url={str(match.get('source_url'))[:60]} -> {str(fast_url)[:60]}"
            )
            if not fast_url:
                continue
            try:
                from app.database import upsert_video, upsert_episode
                item = {k: match.get(k) for k in (
                    "title", "type", "cover", "description", "year", "area",
                    "director", "actors", "rating", "source", "source_url",
                    "genre", "hits", "hits_week", "douban_score", "remarks",
                )}
                item["source"] = src.name
                vid = upsert_video(item)
                if vid:
                    upsert_episode(vid, {
                        "episode_num": episode or 1,
                        "episode_title": "",
                        "play_url": fast_url,
                    })
            except Exception:
                pass
            additions.append({
                "video_id": None,
                "source": src.name,
                "play_url": fast_url,
                "episode_title": "",
                "current": False,
                "headers": fast_header or {},
            })
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False)
    return additions


def _upgrade_current_source(cache_key, video_id, current_source, current_source_url, episode):
    """Upgrade the current source line to its fastest play url (once per session)."""
    try:
        from app.source_framework.registry import get_source_by_name
        csrc = get_source_by_name(current_source)
        if not csrc or not hasattr(csrc, "resolve_play_lines"):
            return
        fast_url, fast_speed, fast_header = _pick_fastest_play_url(csrc, current_source_url, episode, 6.0)
        if not fast_url:
            return
        try:
            from app.database import upsert_episode
            upsert_episode(video_id, {
                "episode_num": episode or 1,
                "episode_title": "",
                "play_url": fast_url,
            })
        except Exception:
            pass
        with _LINES_LOCK:
            cached = _LINES_CACHE.get(cache_key)
            lines = list(cached[1]) if cached else []
            changed = False
            found = False
            for line in lines:
                if line.get("source") == current_source:
                    found = True
                    if not line.get("speed_kbs") or (fast_speed or 0) > (line.get("speed_kbs") or 0):
                        line["play_url"] = fast_url
                        line["kind"] = "direct"
                        line["error"] = None
                        line["speed_kbs"] = fast_speed
                        line["ttfb_ms"] = None
                        line["headers"] = fast_header or {}
                        changed = True
                    break
            if not found:
                lines.append({
                    "source": current_source, "play_url": fast_url,
                    "episode_title": "", "current": True, "kind": "direct",
                    "speed_kbs": fast_speed, "ttfb_ms": None,
                    "error": None, "headers": fast_header or {}, "use_proxy": True,
                })
                changed = True
            if changed:
                lines.sort(key=lambda x: (-(x.get("speed_kbs") or -1), 0 if x.get("current") else 1))
                _store_cache_locked(cache_key, lines[:12])
    except Exception as e:
        logger.debug(f"current source upgrade failed: {e}")


def _merge_lines(cache_key, measured):
    """Merge newly measured lines into cache and bump revision. Returns added count."""
    added = 0
    if not measured:
        return added
    with _LINES_LOCK:
        cached = _LINES_CACHE.get(cache_key)
        lines = list(cached[1]) if cached else []
        existing_urls = {l.get("play_url") for l in lines}
        for m in measured:
            if m.get("play_url") and m["play_url"] not in existing_urls:
                lines.append(m)
                existing_urls.add(m["play_url"])
                added += 1
        if added:
            lines.sort(key=lambda x: (-(x.get("speed_kbs") or -1), 0 if x.get("current") else 1))
            _store_cache_locked(cache_key, lines[:12])
            logger.info(f"play-lines supplement: {cache_key} +{added} lines")
    return added


def _supplement_session(cache_key, video_id, title, norm, episode, current_source, current_source_url=""):
    """Continuous background supplement: search unsearched sources round by round."""
    session_start = time.time()
    current_upgraded = False
    logger.info(f"play-lines session start: {cache_key} title={title}")
    try:
        while time.time() - session_start < _SUPPLEMENT_SESSION_TTL:
            with _LINES_LOCK:
                # 视频已停止播放（前端不再轮询）时，尽快让出补充槽位
                stale = time.time() - _LAST_REQUEST.get(cache_key, 0) > _SUPPLEMENT_STALE_AFTER
                if stale and time.time() - session_start > _SUPPLEMENT_MIN_RUN:
                    break
                sources = _next_sources_locked(cache_key, _SUPPLEMENT_ROUND_SOURCES)
            if not sources:
                break
            matches = _search_round(sources, title, norm, current_source)
            logger.info(
                f"play-lines round: {cache_key} searched={len(sources)} "
                f"matches={len(matches)} sources={[s.name for s in sources]}"
            )
            with _LINES_LOCK:
                now = time.time()
                searched = _SEARCHED.setdefault(cache_key, {})
                for s in sources:
                    searched[s.name] = now
            if not current_upgraded and current_source and current_source_url:
                _upgrade_current_source(cache_key, video_id, current_source, current_source_url, episode)
                current_upgraded = True
            if matches:
                additions = _resolve_matches(matches, episode)
                logger.info(
                    f"play-lines resolve: {cache_key} matches={len(matches)} additions={len(additions)}"
                )
                if additions:
                    measured = _measure_candidates(additions, min(6.0, cfg.PLAY_LINES_MEASURE_TIMEOUT))
                    _merge_lines(cache_key, measured)
        if not current_upgraded and current_source and current_source_url:
            _upgrade_current_source(cache_key, video_id, current_source, current_source_url, episode)
    except Exception as e:
        logger.debug(f"play-lines supplement failed: {e}")
    finally:
        with _LINES_LOCK:
            _SUPPLEMENTING.discard(cache_key)
            _SUPPLEMENT_SESSION_START.pop(cache_key, None)
            _start_next_supplement_locked()
        logger.info(f"play-lines session end: {cache_key}")


def get_play_lines(video_id: int, episode: int | None = None, refresh: bool = False,
                   since_revision: int | None = None) -> dict:
    """Get play-lines: local candidates -> speed sort -> background continuous supplement."""
    cache_key = f"{video_id}:{episode or 0}"
    with _LINES_LOCK:
        _LAST_REQUEST[cache_key] = time.time()
        cached = _LINES_CACHE.get(cache_key)
        if cached and not refresh and time.time() - cached[0] < _LINES_TTL:
            if since_revision is not None and since_revision == cached[2]:
                return {"lines": [], "cached": True, "revision": cached[2], "changed": False}
            return {"lines": cached[1], "cached": True, "revision": cached[2], "changed": True}

    candidates = _gather_candidates(video_id, episode, cfg.PLAY_LINES_LIMIT)
    if not candidates:
        return {"lines": [], "cached": False, "revision": 0, "changed": True}
    lines = _measure_candidates(candidates, cfg.PLAY_LINES_MEASURE_TIMEOUT)
    with _LINES_LOCK:
        # 缓存过期重建：重置已搜源记录，允许重新发起补充（避免“线路永远不变”）
        _SEARCHED.pop(cache_key, None)
        revision = _store_cache_locked(cache_key, lines)

    # Queue continuous cross-source supplement (drpy included) without blocking response
    detail = get_video_detail(video_id)
    title = (detail or {}).get("title", "")
    norm = normalize_title(title)
    current_source = (detail or {}).get("source", "")
    if norm:
        _schedule_supplement(cache_key, video_id, title, norm, episode, current_source,
                             (detail or {}).get("source_url", ""))

    # Background sniff non-direct candidates (non-blocking)
    need_resolve = any(
        l.get("play_url") and not l["play_url"].lower().endswith(
            (".m3u8", ".mp4", ".flv", ".ts", ".mkv")
        ) for l in lines
    )
    if need_resolve:
        threading.Thread(
            target=_background_resolve, args=(cache_key, candidates), daemon=True
        ).start()

    return {"lines": lines, "cached": False, "revision": revision, "changed": True}
