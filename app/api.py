"""FastAPI 路由"""
import logging
import subprocess
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import (
    search_videos, get_videos_by_type, get_video_detail,
    get_home_data, get_watch_history, save_watch_history,
)
from app.crawler import get_status as get_crawl_status, run_crawl
from app.player import play as mpv_play, stop as mpv_stop, current_info as player_info
from app.sources import get_all_sources
from app.models import HistoryRecord
from app.maccms_source import (
    get_manager as get_maccms_manager,
    get_maccms_crawlable_sources,
    MaccmsSource,
)

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
):
    """分类浏览"""
    valid_types = {"movie", "tv", "variety", "anime"}
    if type not in valid_types:
        raise HTTPException(400, f"无效类型: {type}，可用: {', '.join(valid_types)}")
    results, total = get_videos_by_type(type, page, page_size)
    return {"results": results, "total": total, "page": page}


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
    2. 并行查 MacCMS 源获取远程结果（3 秒超时）
    3. 合并去重 → 缓存到 DB → 返回
    """
    import concurrent.futures
    import time

    if not q.strip():
        return {"results": [], "total": 0, "page": page}

    # 1. 本地搜索（秒出）
    local_results, _ = search_videos(q, 1, 9999)
    local_seen = {r["source_url"] for r in local_results}

    # 2. 并行远程搜索（每个源最多 3 秒）
    sources = get_maccms_crawlable_sources()
    remote_items = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {pool.submit(src.search, q): src for src in sources}
        for f in concurrent.futures.as_completed(futures, timeout=6):
            try:
                items = f.result(timeout=3)
                for item in items:
                    su = item.get("source_url", "")
                    if su and su not in local_seen:
                        local_seen.add(su)
                        remote_items.append(item)
            except Exception:
                continue

    # 3. 远程结果入库（获得真实 id）
    from app.database import upsert_video, rebuild_fts
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
    rebuild_fts()
    # 4. 重新查本地（包含刚插入的远程结果，带真实 id）
    local_results, total = search_videos(q, page, page_size)
    return {
        "results": local_results,
        "total": total,
        "page": page,
        "from_remote": len(remote_items),
    }


# ─── 视频详情 ───

@app.get("/api/video/{video_id}")
def api_video_detail(video_id: int):
    """视频详情 + 剧集列表"""
    detail = get_video_detail(video_id)
    if not detail:
        raise HTTPException(404, "视频不存在")
    return detail


# ─── 播放 ───

def _get_source_referer(source_name: str) -> str:
    """根据源名称获取对应的 Referer"""
    for s in get_maccms_manager().get_all():
        if s.name == source_name:
            return s.base_url + "/"
    return ""

@app.post("/api/video/{video_id}/play")
def api_play(
    video_id: int,
    episode: int = Query(default=None, description="剧集编号"),
):
    """播放指定视频/剧集"""
    detail = get_video_detail(video_id)
    if not detail:
        raise HTTPException(404, "视频不存在")

    title = detail["title"]
    episode_title = ""
    play_url = ""
    source_name = detail.get("source", "")
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
        all_sources = get_all_sources() + get_maccms_crawlable_sources()
        for src in all_sources:
            try:
                play_url = src.get_play_url(detail["source_url"])
                if play_url:
                    break
            except Exception:
                continue
        if not play_url:
            play_url = detail["source_url"]

    if not play_url:
        raise HTTPException(500, "无法获取播放地址")

    # 确定 Referer（防盗链）
    referer = _get_source_referer(source_name)

    # 启动 MPV
    try:
        mpv_play(play_url, title, episode_title, referer)
    except FileNotFoundError:
        raise HTTPException(500, "播放器未安装，请安装 mpv")
    except Exception as e:
        raise HTTPException(500, f"启动播放器失败: {e}")

    return {
        "success": True,
        "title": title,
        "episode_title": episode_title,
        "play_url": play_url,
    }


# ─── 播放器控制 ───

@app.get("/api/player/status")
def api_player_status():
    """播放器状态"""
    return player_info()


@app.post("/api/player/stop")
def api_player_stop():
    """停止播放"""
    mpv_stop()
    return {"success": True}


@app.post("/api/player/focus")
def api_player_focus():
    """强制 mpv 窗口获取焦点"""
    try:
        subprocess.run(
            'powershell -NoProfile -Command "(new-object -ComObject wscript.shell).AppActivate(\'TV Media Center\')"',
            shell=True, timeout=3, capture_output=True, check=False)
    except Exception:
        pass
    return {"success": True}


# ─── 观看历史 ───

@app.get("/api/history")
def api_history(limit: int = Query(default=20, ge=1, le=100)):
    """观看历史"""
    return get_watch_history(limit)


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
    return {
        "sources": [
            {"name": s.name, "base_url": s.base_url, "enabled": s.enabled, "type": "html"}
            for s in html_sources
        ] + [
            {"name": s.name, "base_url": s.base_url, "enabled": s.enabled, "type": "maccms"}
            for s in maccms_sources
        ]
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


# ─── 分类列表 ───

@app.get("/api/categories")
def api_categories():
    """可用分类"""
    return {
        "categories": [
            {"key": "movie", "name": "电影", "icon": "🎬"},
            {"key": "tv", "name": "电视剧", "icon": "📺"},
            {"key": "variety", "name": "综艺", "icon": "🎤"},
            {"key": "anime", "name": "动漫", "icon": "🌸"},
        ]
    }
