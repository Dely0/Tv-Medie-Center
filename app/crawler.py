"""爬虫引擎 — 调度、执行、去重、更新"""
import threading
import time
import logging

from app.database import upsert_video, upsert_episode, rebuild_fts
from app.sources import get_all_sources as get_html_sources
from app.maccms_source import get_maccms_crawlable_sources
from config import CRAWL_INTERVAL

logger = logging.getLogger("crawler")

# 爬虫状态 + 线程锁
_status_lock = threading.Lock()
_status = {"running": False, "last_run": None, "progress": ""}


def get_status() -> dict:
    with _status_lock:
        return _status.copy()


def set_progress(msg: str):
    with _status_lock:
        _status["progress"] = msg
    logger.info(msg)


def _crawl_source(source, cat_map: dict) -> tuple[int, int]:
    """爬取单个源的全部类目"""
    total_v = 0
    total_e = 0
    for cat, cat_name in cat_map.items():
        set_progress(f"[{source.name}] 正在获取 {cat_name} 列表...")
        try:
            items = source.get_list(cat)
        except NotImplementedError:
            continue
        except Exception as e:
            logger.warning(f"[{source.name}] 获取 {cat} 列表失败: {e}")
            continue

        if not items:
            continue

        for item in items:
            detail_url = item.get("source_url", "")
            if not detail_url:
                continue

            set_progress(f"[{source.name}] 解析: {item.get('title', '')[:20]}")
            try:
                video_info, episodes = source.get_detail(detail_url)
            except Exception as e:
                logger.warning(f"解析详情失败 {detail_url}: {e}")
                continue

            if not video_info.get("title"):
                continue
            if not video_info.get("cover") and item.get("cover"):
                video_info["cover"] = item["cover"]

            try:
                video_id = upsert_video(video_info)
            except Exception as e:
                logger.warning(f"写入视频失败: {e}")
                continue
            total_v += 1

            for ep in episodes[:200]:
                try:
                    upsert_episode(video_id, ep)
                    total_e += 1
                except Exception as e:
                    logger.warning(f"写入剧集失败: {e}")
    return total_v, total_e


def backfill_drpy_sources(pages: int = None, max_sources: int = None,
                          progress=None) -> int:
    """drpyS 源轻量回填：每个分类前几页列表直接入库（不拉详情，节省时间）。
    独立于 MacCMS 爬取，可随时后台运行。"""
    from app.source_framework.registry import get_drpy_enabled_sources
    from config import DRPYS_CRAWL_PAGES
    pages = pages or DRPYS_CRAWL_PAGES
    drpy_sources = get_drpy_enabled_sources()
    if max_sources:
        drpy_sources = drpy_sources[:max_sources]
    total = 0
    for src in drpy_sources:
        try:
            if progress:
                progress(f"[drpy] 回填: {src.name}")
            for cat in ("movie", "tv", "variety", "anime"):
                for pg in range(1, pages + 1):
                    try:
                        items = src.list_page(cat, pg, pagesize=60)
                    except Exception as e:
                        logger.warning(f"[drpy] {src.name} 分类 {cat} 第{pg}页失败: {e}")
                        break
                    if not items:
                        break
                    for item in items:
                        if not item.get("source_url"):
                            continue
                        title = (item.get("title") or "").strip()
                        if not title or any(t in title for t in ("无数据", "防无限请求", "暂无内容", "加载失败")):
                            continue
                        try:
                            upsert_video(item)
                            total += 1
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"[drpy] 回填源 {src.name} 失败: {e}")
    logger.info(f"drpy 回填完成: 共更新 {total} 条")
    return total


def start_drpy_backfill(pages: int = 2, max_sources: int = None,
                        delay_seconds: float = 3.0):
    """启动独立后台线程执行 drpy 回填（不阻塞主服务）。"""

    def run():
        import time as _time
        _time.sleep(delay_seconds)
        try:
            backfill_drpy_sources(pages=pages, max_sources=max_sources,
                                  progress=set_progress)
            rebuild_fts()
        except Exception as e:
            logger.error(f"drpy 回填异常: {e}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    logger.info(f"drpy 回填线程已启动（pages={pages}）")
    return t


def run_crawl():
    """同步执行爬取（在线程中运行）"""
    with _status_lock:
        if _status["running"]:
            logger.info("爬取已在运行中，跳过")
            return
        _status["running"] = True
        _status["progress"] = "开始爬取..."

    start_time = time.time()
    sources = []
    try:
        sources = get_html_sources() + get_maccms_crawlable_sources()
    except Exception:
        pass

    if not sources:
        logger.info("没有启用的视频源，请检查配置文件")
        with _status_lock:
            _status["running"] = False
            _status["progress"] = "没有启用的视频源，请检查配置文件"
        return

    cat_map = {"movie": "电影", "tv": "电视剧", "variety": "综艺", "anime": "动漫"}
    total_v = 0
    total_e = 0

    try:
        for source in sources:
            set_progress(f"开始爬取源: {source.name}")
            try:
                sv, se = _crawl_source(source, cat_map)
                total_v += sv
                total_e += se
            except Exception as e:
                logger.error(f"爬取源 {source.name} 失败: {e}")

        # drpyS 轻量回填（每日爬取时随主爬虫再跑一轮）
        try:
            total_v += backfill_drpy_sources(pages=DRPYS_CRAWL_PAGES, progress=set_progress)
        except Exception as e:
            logger.warning(f"drpy 回填失败: {e}")

        rebuild_fts()
        elapsed = time.time() - start_time
        set_progress(f"爬取完成! 耗时 {elapsed:.0f}秒, 共 {total_v} 部视频, {total_e} 集")
        with _status_lock:
            _status["last_run"] = time.time()
    except Exception as e:
        logger.error(f"爬取异常: {e}")
        set_progress(f"爬取失败: {e}")
    finally:
        with _status_lock:
            _status["running"] = False


def start_crawler_scheduler():
    """启动爬虫定时器（后台线程）"""

    def scheduler():
        # 启动后先爬一次
        logger.info("首次启动爬取...")
        run_crawl()

        # 定时爬取
        while True:
            time.sleep(CRAWL_INTERVAL)
            logger.info("定时爬取触发...")
            run_crawl()

    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    logger.info(f"爬虫调度器已启动，间隔 {CRAWL_INTERVAL // 3600} 小时")


def backfill_rank_fields(max_pages: int = 5):
    """轻量回填排行/题材字段：直接读取源站列表页（自带 hits/评分/题材），不拉详情"""
    sources = get_maccms_crawlable_sources()
    total = 0
    seen_urls = set()
    for src in sources:
        for cat in ("movie", "tv", "variety", "anime"):
            for pg in range(1, max_pages + 1):
                try:
                    items = src.get_list_page(cat, pg)
                except Exception as e:
                    logger.warning(f"[{src.name}] 列表页 {cat} 第 {pg} 页失败: {e}")
                    continue
                if not items:
                    break
                for item in items:
                    su = item.get("source_url", "")
                    if not su or su in seen_urls:
                        continue
                    seen_urls.add(su)
                    try:
                        upsert_video(item)
                        total += 1
                    except Exception:
                        continue
    rebuild_fts()
    logger.info(f"排行字段回填完成，共更新 {total} 条")
    return total
