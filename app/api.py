"""FastAPI 路由"""
import logging
import concurrent.futures
import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import (
    search_videos, get_videos_by_type, get_video_detail,
    get_home_data, get_watch_history, save_watch_history,
    upsert_video, get_related, get_genres,
)
from app.crawler import get_status as get_crawl_status, run_crawl
from app.sources import get_all_sources
from app.models import HistoryRecord
from app.maccms_source import (
    get_manager as get_maccms_manager,
    get_maccms_crawlable_sources,
    MaccmsSource,
)

# 直接以 uvicorn app.api:app 启动时也自动加载 MacCMS 源配置
import os as _os
from app.maccms_source import load_sources as _load_sources
_MACCMS_CFG = _os.path.join(_os.path.dirname(__file__), "..", "data", "maccms_sources.json")
if _os.path.exists(_MACCMS_CFG):
    _load_sources(_MACCMS_CFG)

from config import SEARCH_TIMEOUT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("api")

app = FastAPI(title="TV Media Center")


# ─── 静态文件 ───
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/css", StaticFiles(directory=os.path.join(_static_dir, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(_static_dir, "js")), name="js")

@app.get("/")
def index():
    return FileResponse(os.path.join(_static_dir, "index.html"))


# ─── 首页 ───

@app.get("/api/home")
def api_home():
    """首页数据（分类+推荐+最近更新）"""
    data = get_home_data()
    return data


# ─── 浏览 ───

@app.get("/api/browse")
def api_browse(
    type: str = Query(default="movie", description="类型"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    genre: str = Query(default="", description="题材筛选"),
    area: str = Query(default="", description="地区筛选"),
    year: str = Query(default="", description="年份/年代筛选"),
):
    """分类浏览"""
    valid_types = {"movie", "tv", "variety", "anime", "recent", "adult"}
    if type not in valid_types:
        raise HTTPException(400, f"无效类型: {type}，可用: {', '.join(valid_types)}")
    syncing = False
    if type == "adult":
        # 成人源已开启但本地内容不足时，自动后台同步（不阻塞本次请求）
        from app.adult import source_names, sync_status, sync_adult_content
        if source_names():
            st = sync_status()
            if st.get("count", 0) < 30 and not st.get("running"):
                import threading
                threading.Thread(target=sync_adult_content, daemon=True).start()
                syncing = True
            elif st.get("running"):
                syncing = True
    results, total = get_videos_by_type(type, page, page_size, genre, area, year)
    if syncing:
        return {"results": results, "total": total, "page": page, "syncing": True}
    return {"results": results, "total": total, "page": page}


@app.get("/api/genres")
def api_genres(
    type: str = Query(default="movie", description="类型"),
):
    """某分类下的筛选维度：tags(题材) / areas(地区) / years(年份)"""
    data = get_genres(type)
    # 兼容旧字段
    data["genres"] = data["tags"]
    return data


# ─── 搜索 ───

@app.get("/api/search")
def api_search(
    q: str = Query(default="", description="搜索关键词"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    """
    搜索逻辑：
    1. 先查本地 SQLite（已同步/搜索过的缓存）
    2. 并行查 MacCMS 源获取远程结果（每个源最多 SEARCH_TIMEOUT 秒）
    3. 合并去重 → 缓存到 DB → 返回
    """
    if not q.strip():
        return {"results": [], "total": 0, "page": page}

    # 成人开关开启时搜索包含成人内容，关闭时始终排除
    from app.adult import is_enabled
    include_adult = is_enabled()

    # 1. 本地搜索（秒出）
    local_results, _ = search_videos(q, 1, 9999, include_adult=include_adult)
    local_seen = {r["source_url"] for r in local_results}

    # 2. 并行远程搜索
    from app.source_framework.registry import get_search_sources
    sources = get_search_sources()
    remote_items = []

    if sources:
        wall_timeout = SEARCH_TIMEOUT + 3  # 给 fallback 留余量
        deadline = time.time() + wall_timeout + 2
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sources), 4))
        futures = {pool.submit(src.search, q, SEARCH_TIMEOUT): src for src in sources}
        try:
            for f in concurrent.futures.as_completed(futures, timeout=wall_timeout):
                try:
                    items = f.result(timeout=max(0, deadline - time.time()))
                    for item in items:
                        su = item.get("source_url", "")
                        if su and su not in local_seen:
                            local_seen.add(su)
                            remote_items.append(item)
                except concurrent.futures.TimeoutError:
                    logger.warning(f"搜索单个源超时")
                except Exception:
                    continue
        except concurrent.futures.TimeoutError:
            logger.warning(f"搜索整体超时 ({wall_timeout}s), 已获取 {len(remote_items)} 条远程结果")
        finally:
            # 不等待未完成任务（慢源由后台自行结束），避免搜索接口卡住
            pool.shutdown(wait=False)
        logger.info(f"搜索「{q}」: 本地 {len(local_results)} 条 + 远程 {len(remote_items)} 条")

    # 3. 远程结果入库（获得真实 id）
    for item in remote_items:
        try:
            upsert_video({
                "title": item.get("title", ""),
                "type": item.get("type", "movie"),
                "cover": item.get("cover", ""),
                "description": (item.get("description") or "")[:300],
                "year": item.get("year"),
                "area": item.get("area", ""),
                "director": (item.get("director") or "")[:100],
                "actors": (item.get("actors") or "")[:200],
                "rating": item.get("rating"),
                "source": item.get("source", ""),
                "source_url": item.get("source_url", ""),
            })
        except Exception:
            pass
    # 4. 重新查本地（包含刚插入的远程结果，带真实 id）
    local_results, total = search_videos(q, page, page_size, include_adult=include_adult)
    return {
        "results": local_results,
        "total": total,
        "page": page,
        "from_remote": len(remote_items),
    }


# ─── 视频详情 ───

@app.get("/api/video/{video_id}")
def api_video_detail(video_id: int):
    """视频详情 + 剧集列表（如果本地无剧集，尝试从源实时获取）"""
    detail = get_video_detail(video_id)
    if not detail:
        raise HTTPException(404, "视频不存在")

    # 如果本地没有剧集，尝试从远程源实时拉取
    if not detail.get("episodes"):
        logger.info(f"本地无剧集, 尝试从远程源拉取: {detail.get('title')}")
        from app.database import upsert_episode
        source_url = detail.get("source_url", "")
        source_name = detail.get("source", "")
        fetched = False

        # 优先用同名的源
        if source_name:
            from app.source_framework.registry import get_source_by_name, get_search_sources
            src = get_source_by_name(source_name)
            if src:
                try:
                    info, episodes = src.get_detail(source_url)
                    if episodes:
                        for ep in episodes:
                            upsert_episode(video_id, ep)
                        fetched = True
                        logger.info(f"从 {source_name} 拉取到 {len(episodes)} 集")
                except Exception:
                    pass

        # 如果同名源没拉到, 遍历所有源
        if not fetched:
            for src in get_search_sources():
                if src.name == source_name:
                    continue
                try:
                    info, episodes = src.get_detail(source_url)
                    if episodes:
                        for ep in episodes:
                            upsert_episode(video_id, ep)
                        fetched = True
                        logger.info(f"从 {src.name} 拉取到 {len(episodes)} 集")
                        break
                except Exception:
                    continue

        # 重新查询（包含刚入库的剧集）
        if fetched:
            detail = get_video_detail(video_id)

    detail["related"] = get_related(video_id)
    return detail


# ─── 播放 ───

_MEDIA_EXTS = (".mp4", ".m3u8", ".flv", ".ts", ".mkv")


def _is_media_url(url: str) -> bool:
    """判断是否为可直接播放的媒体地址（忽略查询参数）"""
    from urllib.parse import urlparse
    return urlparse(url).path.lower().endswith(_MEDIA_EXTS)


@app.post("/api/video/{video_id}/play")
def api_play(
    video_id: int,
    episode: int = Query(default=None, description="剧集编号"),
    start_seconds: float = Query(default=0, ge=0, description="续播起始秒数"),
):
    """解析指定视频/剧集的真实播放地址（实际播放由前端完成）"""
    detail = get_video_detail(video_id)
    if not detail:
        raise HTTPException(404, "视频不存在")

    title = detail["title"]
    episode_title = ""
    play_url = ""
    eps = detail.get("episodes") or []

    # 1. 如果有剧集，直接取第一集或指定集
    if eps:
        ep = None
        if episode:
            ep = next((e for e in eps if e["episode_num"] == episode), None)
        if not ep:
            ep = eps[0]
        episode_title = ep.get("episode_title", f"第{ep['episode_num']}集")
        play_url = ep.get("play_url", "")

    # 2. 没有剧集时才尝试从源解析（电影无剧集的情况）
    if not play_url:
        from app.source_framework.registry import get_search_sources
        all_sources = get_all_sources() + get_search_sources()
        for src in all_sources:
            try:
                resolved = src.get_play_url(detail["source_url"])
            except Exception:
                continue
            # 源解析失败时会把原 URL 原样返回，必须排除详情页地址
            if resolved and (resolved != detail["source_url"] or _is_media_url(resolved)):
                play_url = resolved
                break
    if not play_url:
        raise HTTPException(500, "无法获取播放地址")

    # 提供 Referer 供前端源站测速使用
    referer = ""
    for s in get_maccms_manager().get_all():
        if s.name == detail.get("source", ""):
            referer = s.base_url + "/"
            break

    return {
        "success": True,
        "title": title,
        "episode_title": episode_title,
        "play_url": play_url,
        "source": detail.get("source", ""),
        "referer": referer,
        "start_seconds": start_seconds,
    }


@app.get("/api/video/{video_id}/alternates")
def api_video_alternates(
    video_id: int,
    episode: int = Query(default=1, ge=1, description="剧集编号"),
):
    """当前视频在其他源的备用播放地址（播放失败自动换源时调用）"""
    detail = get_video_detail(video_id)
    if not detail:
        raise HTTPException(404, "视频不存在")

    title = (detail.get("title") or "").strip()
    if not title:
        return {"alternates": []}

    current_source = detail.get("source", "")
    from app.source_framework.registry import get_search_sources
    sources = get_search_sources()
    alternates = []

    def find_alternate(src):
        if not src or src.name == current_source:
            return None
        try:
            items = src.search(title, timeout=6)
            match = None
            for it in items:
                t = (it.get("title") or "").strip()
                if not t:
                    continue
                if t == title or (len(t) >= 2 and (t in title or title in t)):
                    match = it
                    break
            if not match:
                return None
            _, episodes = src.get_detail(match.get("source_url", ""))
            if not episodes:
                return None
            ep = None
            if episode:
                ep = next((e for e in episodes if e.get("episode_num") == episode), None)
            if not ep:
                ep = episodes[0]
            play_url = (ep or {}).get("play_url", "")
            # 只返回可直接播放的媒体地址（HLS/MP4），排除 HTML 播放页
            if not play_url or not _is_media_url(play_url):
                return None
            return {
                "source": src.name,
                "play_url": play_url,
                "episode_title": (ep or {}).get("episode_title", ""),
            }
        except Exception:
            return None

    if sources:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sources), 4))
        futures = [pool.submit(find_alternate, s) for s in sources]
        try:
            for f in concurrent.futures.as_completed(futures, timeout=15):
                try:
                    r = f.result(timeout=1)
                except Exception:
                    continue
                if r:
                    alternates.append(r)
        except concurrent.futures.TimeoutError:
            logger.warning(f"查找备用源超时，已找到 {len(alternates)} 个")
        finally:
            pool.shutdown(wait=False)

    return {"alternates": alternates[:4]}


@app.get("/api/video/{video_id}/best-source")
def api_video_best_source(
    video_id: int,
    episode: int = Query(default=None, description="剧集编号"),
):
    """多源测速优选：找出同一视频在各源中速度最快、码率最高的播放地址。
    CDN 测速结果缓存 10 分钟。"""
    from app.source_selector import find_best_source
    return find_best_source(video_id, episode)


@app.get("/api/video/{video_id}/play-lines")
def api_video_play_lines(
    video_id: int,
    episode: int = Query(default=None, description="剧集编号"),
    refresh: bool = Query(default=False, description="强制重新测速"),
):
    """播放链：同一视频在所有启用源中的候选线路（测速排序），前端播放失败/卡顿自动切换。"""
    from app.source_framework.play_lines import get_play_lines
    return get_play_lines(video_id, episode, refresh=refresh)


@app.get("/api/probe")
def api_probe(
    url: str = Query(default="", description="播放地址"),
    referer: str = Query(default="", description="防盗链来源"),
):
    """探测播放源：拉取清单并下载首个分片，返回首字节延迟与下载速度（异步调用，不阻塞播放）"""
    import random
    import re
    import time
    import urllib.request
    import urllib.parse

    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "无效地址")

    from config import USER_AGENTS
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    if referer:
        headers["Referer"] = referer

    out = {"ttfb_ms": None, "speed_mbs": None, "bytes": 0, "error": None, "segment_url": ""}

    def fetch(u, cap, timeout):
        t0 = time.time()
        req = urllib.request.Request(u, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        ttfb = (time.time() - t0) * 1000
        data = resp.read(cap)
        return data, ttfb, time.time() - t0

    try:
        data, ttfb, _ = fetch(url, 262144, 6)
        out["ttfb_ms"] = round(ttfb)
        text = data.decode("utf-8", "replace")
        seg_url = ""
        if "#EXT-X-STREAM-INF" in text:
            # master 清单 → 先取媒体清单，再取分片
            m = re.search(r"#EXT-X-STREAM-INF[^\n]*\n\s*(\S+)", text)
            if m:
                media_url = urllib.parse.urljoin(url, m.group(1).strip())
                try:
                    mdata, _, _ = fetch(media_url, 262144, 6)
                    mtext = mdata.decode("utf-8", "replace")
                    m2 = re.search(r"#EXTINF[^\n]*\n\s*(\S+)", mtext)
                    if m2:
                        seg_url = urllib.parse.urljoin(media_url, m2.group(1).strip())
                except Exception:
                    pass
        elif "#EXTINF" in text:
            m = re.search(r"#EXTINF[^\n]*\n\s*(\S+)", text)
            if m:
                seg_url = urllib.parse.urljoin(url, m.group(1).strip())

        if seg_url:
            out["segment_url"] = seg_url[:120]
            sdata, _, dur = fetch(seg_url, 524288, 8)
            out["bytes"] = len(sdata)
            if dur > 0:
                out["speed_mbs"] = round(len(sdata) / dur / 1048576, 2)
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


# ---------- 本地代理：防盗链/跨域 HLS 与媒体转发 ----------

@app.get("/api/hls-proxy")
def api_hls_proxy(
    url: str = Query(default="", description="m3u8 地址"),
    ref: str = Query(default="", description="防盗链 Referer"),
    ua: str = Query(default="", description="User-Agent"),
    origin: str = Query(default="", description="Origin"),
    cookie: str = Query(default="", description="Cookie"),
):
    from app.net.proxy import hls_proxy
    return hls_proxy(url, ref, ua, origin, cookie)


@app.get("/api/media-proxy")
def api_media_proxy(
    url: str = Query(default="", description="媒体文件地址"),
    ref: str = Query(default="", description="防盗链 Referer"),
    ua: str = Query(default="", description="User-Agent"),
    origin: str = Query(default="", description="Origin"),
    cookie: str = Query(default="", description="Cookie"),
    range: str = Query(default="", description="Range 头"),
):
    from app.net.proxy import media_proxy
    return media_proxy(url, ref, ua, origin, cookie, range)


# ---------- 源运维：健康检查 / 社区订阅 ----------

@app.get("/api/ops/status")
def api_ops_status():
    """源健康状态与隔离情况。"""
    from app.ops.health import get_status
    return get_status()


@app.post("/api/ops/run-health-check")
def api_ops_run_health_check():
    """手动触发全量源健康检查（后台执行）。"""
    from app.ops.health import run_health_check
    import threading
    threading.Thread(target=run_health_check, daemon=True).start()
    return {"success": True, "message": "健康检查已在后台启动"}


@app.post("/api/ops/sync-now")
def api_ops_sync_now():
    """手动触发社区 TVBox 订阅同步（后台执行）。"""
    from app.ops.sync import sync_now
    return sync_now()


# ---------- drpyS 爬虫生态 ----------

@app.get("/api/drpy/status")
def api_drpy_status():
    """drpyS 源列表与启用状态。"""
    from app.source_framework.drpy_source import get_registry, recover_name
    from app.ops.health import is_source_dead
    from app.sidecar.drpys import is_ready
    reg = get_registry()
    sources = []
    for s in reg.get_all():
        sources.append({
            "key": s.key,
            "name": s.name,
            "api": s.api_url,
            "type": s.type,
            "enabled": s.enabled,
            "adult": s.adult,
            "searchable": s.searchable,
            "dead": is_source_dead(s.name),
        })
    return {
        "sidecar_ready": is_ready(),
        "total": len(sources),
        "enabled": sum(1 for s in sources if s["enabled"]),
        "adult": sum(1 for s in sources if s["adult"]),
        "sources": sources,
    }


@app.post("/api/drpy/refresh")
def api_drpy_refresh():
    """强制刷新 drpyS 源注册表（后台执行）。"""
    from app.source_framework.drpy_source import refresh_registry
    import threading
    ok = {}

    def run():
        nonlocal ok
        try:
            ok = {"success": refresh_registry(force=True)}
        except Exception as e:
            ok = {"error": str(e)}

    threading.Thread(target=run, daemon=True).start()
    return {"success": True, "message": "drpyS 源注册表刷新已启动"}


@app.post("/api/drpy/backfill")
def api_drpy_backfill(
    pages: int = Query(default=2, ge=1, le=10, description="每个分类回填页数"),
):
    """手动触发 drpy 源内容回填（后台执行）。"""
    from app.crawler import start_drpy_backfill
    start_drpy_backfill(pages=pages)
    return {"success": True, "message": f"drpy 回填已启动（每分类 {pages} 页）"}


# ─── 观看历史 ───

@app.get("/api/history")
def api_history(
    limit: int = Query(default=20, ge=1, le=100),
    adult: bool = Query(default=False, description="是否只返回成人内容历史"),
):
    """观看历史（默认排除成人内容；adult=1 时只返回成人内容历史）"""
    return {"items": get_watch_history(limit, adult=adult)}


@app.post("/api/history/clean-adult")
def api_history_clean_adult():
    """清理观看历史中的成人内容记录（删除前自动备份到 watch_history_adult_backup 表）"""
    from app.database import delete_adult_history
    n = delete_adult_history()
    return {"success": True, "deleted": n}


@app.post("/api/history")
def api_save_history(record: HistoryRecord):
    """保存观看进度"""
    save_watch_history(record.video_id, record.episode_id, record.progress_seconds, record.total_seconds)
    return {"success": True}


# ─── 爬虫控制 ───

@app.get("/api/crawl/status")
def api_crawl_status():
    """爬虫状态"""
    return get_crawl_status()


@app.post("/api/crawl/trigger")
def api_trigger_crawl():
    """手动触发爬取"""
    import threading
    t = threading.Thread(target=run_crawl, daemon=True)
    t.start()
    return {"success": True, "message": "爬取已启动"}


# ─── 视频源管理 ───

@app.get("/api/sources")
def api_sources():
    """所有已配置的视频源"""
    html_sources = get_all_sources()
    maccms_sources = get_maccms_manager().get_all()
    from app.ops.health import _read as _health_read
    from app.source_framework.drpy_source import get_registry
    health = _health_read()
    drpy_sources = [
        {
            "name": s.name,
            "base_url": s.api_url,
            "enabled": s.enabled,
            "type": "drpy",
            "adult": s.adult,
            "health": health.get(s.name, {}),
        }
        for s in get_registry().get_all()
    ]
    return {
        "sources": [
            {"name": s.name, "base_url": s.base_url, "enabled": s.enabled, "type": "html"}
            for s in html_sources
        ] + [
            {
                "name": s.name,
                "base_url": s.base_url,
                "enabled": s.enabled,
                "type": "maccms",
                "health": health.get(s.name, {}),
            }
            for s in maccms_sources
        ] + drpy_sources
    }


# ─── MacCMS 源管理 ───

@app.get("/api/maccms/sources")
def api_maccms_sources():
    """MacCMS 源列表"""
    sources = get_maccms_manager().get_all()
    return {
        "sources": [
            {
                "name": s.name,
                "base_url": s.base_url,
                "category_map": s.category_map,
            }
            for s in sources
        ]
    }


@app.post("/api/maccms/test")
def api_maccms_test(
    base_url: str = Query(default="", description="源地址"),
    source_name: str = Query(default="测试源", description="源名称"),
):
    """测试一个 MacCMS 源是否可用"""
    if not base_url:
        raise HTTPException(400, "请提供 base_url")
    source = MaccmsSource(name=source_name, base_url=base_url)
    try:
        data = source._request({"ac": "list", "t": "1", "pagesize": "5"})
        if not data:
            return {"success": False, "message": "API 无响应，可能不是有效的 MacCMS 站点"}
        items = data.get("list") or []
        return {
            "success": True,
            "message": f"成功获取 {len(items)} 个视频",
            "sample": [
                {"title": i.get("vod_name"), "cover": i.get("vod_pic")}
                for i in items[:3]
            ],
            "total": data.get("total", 0),
        }
    except Exception as e:
        return {"success": False, "message": f"测试失败: {e}"}


@app.post("/api/maccms/save")
def api_maccms_save(
    name: str = Query(default=""),
    base_url: str = Query(default=""),
    movie_id: str = Query(default="1"),
    tv_id: str = Query(default="2"),
    variety_id: str = Query(default="3"),
    anime_id: str = Query(default="4"),
):
    """保存一个新 MacCMS 源到配置文件"""
    if not name or not base_url:
        raise HTTPException(400, "请提供 name 和 base_url")
    import json, os
    config_path = os.path.join(os.path.dirname(__file__), "..", "data", "maccms_sources.json")
    config = {"sources": []}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    # 去重
    config["sources"] = [s for s in config["sources"] if s.get("base_url") != base_url]
    config["sources"].append({
        "name": name,
        "base_url": base_url.rstrip("/"),
        "enabled": True,
        "category_map": {
            "movie": movie_id,
            "tv": tv_id,
            "variety": variety_id,
            "anime": anime_id,
        },
    })
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    # 重新加载
    get_maccms_manager().load_from_config(config_path)
    return {"success": True, "message": f"源 '{name}' 已保存，请触发爬取以获取数据"}


@app.post("/api/maccms/update-remote")
def api_maccms_update_remote():
    """从远程仓库拉取最新视频源配置并热加载（快速换源）"""
    from app.source_updater import update_sources_from_remote
    return update_sources_from_remote()


@app.post("/api/douban/sync")
def api_douban_sync():
    """手动同步豆瓣热播榜到本地（匹配入库 + 回填评分，后台执行）"""
    from app.douban import sync_douban_hot
    import threading
    result = {}

    def run():
        nonlocal result
        try:
            result = sync_douban_hot()
        except Exception as e:
            result = {"error": str(e)}

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return {"success": True, "message": "豆瓣同步已在后台启动"}


# ─── 分类列表 ───

@app.get("/api/categories")
def api_categories():
    """可用分类"""
    cats = [
        {"key": "movie", "name": "电影", "icon": "🎬"},
        {"key": "tv", "name": "电视剧", "icon": "📺"},
        {"key": "variety", "name": "综艺", "icon": "🎤"},
        {"key": "anime", "name": "动漫", "icon": "🌸"},
    ]
    from app.adult import is_enabled
    if is_enabled():
        cats.append({"key": "adult", "name": "成人", "icon": "🔞"})
    return {"categories": cats}


@app.get("/api/config")
def api_config():
    """应用配置（前端启动时读取，用于控制导航显隐等）"""
    from app.adult import is_enabled, source_names
    return {
        "adult_enabled": is_enabled(),
        "adult_sources": source_names(),
    }


@app.post("/api/adult/sync")
def api_adult_sync():
    """手动触发成人内容轻量回填（后台执行）"""
    from app.adult import sync_adult_content
    import threading
    threading.Thread(target=sync_adult_content, daemon=True).start()
    return {"success": True, "message": "成人内容同步已启动"}


@app.get("/api/adult/sync-status")
def api_adult_sync_status():
    from app.adult import sync_status
    return sync_status()
