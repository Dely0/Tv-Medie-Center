"""爬虫引擎 — 调度、执行、去重、更新"""
import asyncio
import threading
import time
import logging
from typing import Optional

from app.database import upsert_video, upsert_episode, rebuild_fts, get_db
from app.sources import get_all_sources as get_html_sources
from app.maccms_source import get_maccms_crawlable_sources
from config import CRAWL_INTERVAL

logger = logging.getLogger("crawler")

# 爬虫状态
_status = {"running": False, "last_run": None, "progress": ""}


def get_status() -> dict:
    return _status.copy()


def set_progress(msg: str):
    _status["progress"] = msg
    logger.info(msg)


async def crawl_source(source) -> tuple[int, int]:
    """爬取单个源的所有分类"""
    total_videos = 0
    total_episodes = 0
    categories = ["movie", "tv", "variety", "anime"]

    for cat in categories:
        set_progress(f"[{source.name}] 正在获取 {cat} 列表...")
        items = source.get_list(cat)
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

            # 补充封面
            if not video_info.get("cover") and item.get("cover"):
                video_info["cover"] = item["cover"]

            # 写入数据库
            try:
                video_id = upsert_video(video_info)
            except Exception as e:
                logger.warning(f"写入视频失败 {video_info.get('title')}: {e}")
                continue

            total_videos += 1

            # 写入剧集
            for ep in episodes[:200]:  # 最多 200 集
                try:
                    upsert_episode(video_id, ep)
                    total_episodes += 1
                except Exception as e:
                    logger.warning(f"写入剧集失败: {e}")

    return total_videos, total_episodes


def run_crawl():
    """同步执行爬取（在线程中运行）"""
    if _status["running"]:
        logger.info("爬取已在运行中，跳过")
        return

    _status["running"] = True
    _status["progress"] = "开始爬取..."
    start_time = time.time()

    try:
        # 合并 HTML 爬虫源 + MacCMS API 源
        sources = get_html_sources() + get_maccms_crawlable_sources()
        if not sources:
            set_progress("没有启用的视频源，请先在 sources/maccms_sources.json 中配置")
            _status["running"] = False
            return

        total_v = 0
        total_e = 0

        for source in sources:
            set_progress(f"开始爬取源: {source.name}")
            try:
                # SourceGeneric 的 get_list/get_detail 是同步的，直接调用
                cat_map = {"movie": "电影", "tv": "电视剧", "variety": "综艺", "anime": "动漫"}
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

                        title = item.get("title", "")[:20]
                        set_progress(f"[{source.name}] 解析: {title}")

                        try:
                            video_info, episodes = source.get_detail(detail_url)
                        except Exception as e:
                            logger.warning(f"解析详情失败 {detail_url}: {e}")
                            continue

                        if not video_info.get("title"):
                            continue

                        # 补充封面
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

            except Exception as e:
                logger.error(f"爬取源 {source.name} 失败: {e}")

        # 重建 FTS 索引
        rebuild_fts()

        elapsed = time.time() - start_time
        set_progress(
            f"爬取完成! 耗时 {elapsed:.0f}秒, "
            f"共 {total_v} 部视频, {total_e} 集"
        )
        _status["last_run"] = time.time()

    except Exception as e:
        set_progress(f"爬取出错: {e}")
        logger.exception("爬取异常")
    finally:
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
