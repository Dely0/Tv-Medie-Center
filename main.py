"""TV Media Center — 入口"""
import os
import time
import threading
import uvicorn
import logging
from app.api import app
from app.database import init_db
from app.crawler import start_crawler_scheduler
from app.maccms_source import load_sources
from config import PORT

logger = logging.getLogger("main")


def _run_online_tasks():
    """后台任务：拉取远程视频源配置 + 同步豆瓣热播榜（失败静默降级）"""
    try:
        from app.source_updater import update_sources_from_remote
        r = update_sources_from_remote()
        if r.get("success"):
            logger.info(f"视频源配置已自动更新: {r.get('count')} 个源")
        else:
            logger.info(f"视频源自动更新跳过: {r.get('error')}")
    except Exception as e:
        logger.warning(f"视频源自动更新失败: {e}")

    try:
        from app.douban import sync_douban_hot
        stats = sync_douban_hot()
        logger.info(f"豆瓣热播同步完成: 匹配 {stats.get('matched')}，未匹配 {stats.get('unmatched')}")
    except Exception as e:
        logger.warning(f"豆瓣同步失败: {e}")

    try:
        from app.database import build_recommend_pool
        n = build_recommend_pool()
        logger.info(f"推荐池构建完成: {n} 条")
    except Exception as e:
        logger.warning(f"推荐池构建失败: {e}")


def start_online_scheduler():
    """启动时立即执行一次，之后每 24 小时执行一次"""
    def loop():
        _run_online_tasks()
        while True:
            time.sleep(24 * 3600)
            _run_online_tasks()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    logger.info("在线任务调度器已启动（远程换源 + 豆瓣榜单，24小时周期）")


def main():
    init_db()
    # 启动 drpyS 侧车（阶段 B：JS 爬虫生态）
    from app.sidecar.drpys import ensure_started
    ensure_started(wait_seconds=20)
    # 安装 DoH 解析回退（系统 DNS 失败时自动使用）
    from app.net import doh
    from config import DOH_ENABLED
    doh.install(DOH_ENABLED)
    # 预热 drpyS 源注册表（拉取 /config/1，供搜索/播放链使用）
    try:
        from app.source_framework.drpy_source import refresh_registry
        refresh_registry()
    except Exception as e:
        logger.warning(f"drpyS 注册表预热失败: {e}")
    # 加载 MacCMS 源配置
    config_path = os.path.join(os.path.dirname(__file__), "data", "maccms_sources.json")
    if os.path.exists(config_path):
        load_sources(config_path)
        from app.maccms_source import get_manager
        count = len(get_manager().get_all())
        logger.info(f"已加载 {count} 个 MacCMS 视频源")
    # 启动爬虫定时器（后台）
    start_crawler_scheduler()
    # 启动在线任务（远程换源 + 豆瓣榜单）
    start_online_scheduler()
    # 启动源健康检查与社区订阅同步（阶段 A）
    from app.ops.health import start_health_scheduler
    from app.ops.sync import start_sync_scheduler
    start_health_scheduler()
    start_sync_scheduler()
    # 启动服务（使用 bat 脚本打开浏览器，会带 --start-fullscreen）
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
