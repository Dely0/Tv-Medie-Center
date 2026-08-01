"""豆瓣数据模块：榜单/相似推荐抓取 + 标题归一化匹配 + 入库。

豆瓣官方 API 早已关闭，这里使用移动端公开的 rexxar 接口（m.douban.com），
仅抓取榜单与推荐数据（标题/评分/标签），用于热播榜与推荐榜。
接口随时可能失效，所有抓取都带超时与重试，调用方需提供降级逻辑。
"""
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("douban")

DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://m.douban.com/",
}

# 豆瓣集合名 → 首页/浏览分类
COLLECTIONS = {
    "movie": "movie_hot",
    "tv": "tv_hot",
    "anime": "tv_animation",
    "variety": "tv_variety_show",
}

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "douban_cache.json")


def _get_json(url: str, timeout: float = 12.0, retries: int = 2):
    """GET JSON，带超时与简单重试；失败返回 None"""
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=DOUBAN_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            if i >= retries - 1:
                logger.warning(f"豆瓣请求失败 {url[:100]}: {e}")
                return None
            time.sleep(1.0)
    return None


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
    except Exception as e:
        logger.warning(f"豆瓣缓存写入失败: {e}")


def fetch_collection(category: str, count: int = 30, use_cache: bool = True) -> list[dict]:
    """抓取豆瓣集合（热门榜）。use_cache=True 时同一天内复用结果。"""
    coll = COLLECTIONS.get(category)
    if not coll:
        return []
    today = time.strftime("%Y%m%d")
    cache_key = f"collection:{coll}:{today}:{count}"
    if use_cache:
        cached = _cache_read().get(cache_key)
        if cached:
            return cached
    url = f"https://m.douban.com/rexxar/api/v2/subject_collection/{coll}/items?start=0&count={count}"
    j = _get_json(url)
    items = []
    for it in (j or {}).get("subject_collection_items", []) or []:
        rating = it.get("rating") or {}
        items.append({
            "douban_id": str(it.get("id") or ""),
            "title": it.get("title") or "",
            "year": it.get("year") or "",
            "score": float(rating.get("value") or 0),
            "score_count": int(rating.get("count") or 0),
            "info": it.get("info") or "",
        })
    if items:
        cache = _cache_read()
        cache[cache_key] = items
        _cache_write(cache)
    return items


def fetch_recommendations(douban_id: str, count: int = 20) -> list[dict]:
    """抓取豆瓣相似影片推荐"""
    if not douban_id:
        return []
    url = f"https://m.douban.com/rexxar/api/v2/subject/{douban_id}/recommendations"
    j = _get_json(url)
    out = []
    for it in (j or [])[:count]:
        rating = it.get("rating") or {}
        out.append({
            "douban_id": str(it.get("id") or ""),
            "title": it.get("title") or "",
            "subtitle": it.get("card_subtitle") or "",
            "score": float(rating.get("value") or 0),
        })
    return out


def fetch_tag_recommend(tag: str, count: int = 20) -> list[dict]:
    """按标签抓豆瓣电影推荐（tags=喜剧 等）"""
    url = ("https://m.douban.com/rexxar/api/v2/movie/recommend"
           f"?start=0&count={count}&tags={urllib.parse.quote(tag)}")
    j = _get_json(url)
    out = []
    for it in (j or {}).get("items", []) or []:
        rating = it.get("rating") or {}
        out.append({
            "douban_id": str(it.get("id") or ""),
            "title": it.get("title") or "",
            "subtitle": it.get("card_subtitle") or "",
            "score": float(rating.get("value") or 0),
        })
    return out


def normalize_title(title: str) -> str:
    """标题归一化：去标点/空白/尾部年份/语言后缀，便于跨站匹配"""
    if not title:
        return ""
    s = str(title)
    # 只删除“尾部括号年份”或“空格分隔年份”，保留片名自带的数字（如“寒战1994”）
    s = re.sub(r"[（(]\d{4}[)）]$", "", s)
    s = re.sub(r"\s+\d{4}$", "", s)
    s = re.sub(r"[!！?？·:：,，.。;；'\"“”‘’、()（）\[\]【】\-—_/\\\s]+", "", s)
    s = re.sub(r"(国语|粤语|台配|中字|字幕|完整版|电影解说|高清)$", "", s)
    return s.lower()


def match_in_local_db(norm_title: str) -> int | None:
    """在本地库按归一化标题匹配，返回 video_id"""
    if not norm_title:
        return None
    from app.database import get_db
    with get_db() as db:
        rows = db.execute("SELECT id, title FROM videos WHERE title != ''").fetchall()
    for r in rows:
        nlocal = normalize_title(r["title"])
        if not nlocal:
            continue
        if norm_title == nlocal or (len(norm_title) >= 4 and (norm_title in nlocal or nlocal in norm_title)):
            return r["id"]
    return None


def search_and_upsert(title: str) -> int | None:
    """源站搜索标题并入库，返回 video_id；找不到返回 None"""
    if not title:
        return None
    from app.database import upsert_video
    from app.maccms_source import get_maccms_crawlable_sources
    norm = normalize_title(title)
    if not norm:
        return None
    sources = get_maccms_crawlable_sources()
    # 优先 360 资源（快源），再其他源
    sources = sorted(sources, key=lambda s: 0 if "360" in s.name else 1)
    for src in sources:
        try:
            items = src.search(title, timeout=6)
        except Exception:
            continue
        for it in items:
            it_title = it.get("title") or ""
            n_it = normalize_title(it_title)
            if not n_it:
                continue
            if norm == n_it or (len(norm) >= 4 and (norm in n_it or n_it in norm)):
                try:
                    video_id = upsert_video({
                        "title": it.get("title", ""),
                        "type": it.get("type", "movie"),
                        "cover": it.get("cover", ""),
                        "description": (it.get("description") or "")[:300],
                        "year": it.get("year"),
                        "area": it.get("area", ""),
                        "director": (it.get("director") or "")[:100],
                        "actors": (it.get("actors") or "")[:200],
                        "rating": it.get("rating"),
                        "source": it.get("source", ""),
                        "source_url": it.get("source_url", ""),
                        "genre": it.get("genre", ""),
                        "remarks": it.get("remarks", ""),
                    })
                    return video_id
                except Exception as e:
                    logger.warning(f"豆瓣匹配入库失败 {it_title}: {e}")
                    return None
    return None


def sync_douban_hot(limit_per_category: int = 30) -> dict:
    """同步豆瓣热门榜到本地：匹配/入库后写入 douban_ranks 表，并回填豆瓣评分"""
    from app.database import get_db
    stats = {"matched": 0, "unmatched": 0, "categories": {}}
    all_rows = []
    for category, items in ((c, fetch_collection(c, limit_per_category)) for c in COLLECTIONS):
        matched = 0
        for rank, it in enumerate(items, 1):
            video_id = match_in_local_db(normalize_title(it["title"]))
            if video_id is None:
                video_id = search_and_upsert(it["title"])
            if video_id:
                matched += 1
                all_rows.append((category, rank, video_id, it["douban_id"],
                                 it["title"], it["score"], it["score_count"]))
        stats["matched"] += matched
        stats["unmatched"] += len(items) - matched
        stats["categories"][category] = {"total": len(items), "matched": matched}

    with get_db() as db:
        db.execute("DELETE FROM douban_ranks")
        for row in all_rows:
            db.execute(
                "INSERT INTO douban_ranks (category, rank, video_id, douban_id, title, score, score_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            if row[5] > 0:
                db.execute("UPDATE videos SET douban_score=? WHERE id=? AND (douban_score IS NULL OR douban_score=0)",
                           (row[5], row[2]))
    logger.info(f"豆瓣热播同步完成: 匹配 {stats['matched']}，未匹配 {stats['unmatched']}")
    return stats
