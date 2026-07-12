"""TV Media Center — 入口"""
import os
import uvicorn
import webbrowser
import threading
import time
import logging
from app.api import app
from app.database import init_db
from app.crawler import start_crawler_scheduler
from app.maccms_source import load_sources
from config import PORT

logger = logging.getLogger("main")


def open_browser():
    """延迟打开浏览器（等服务启动完）"""
    time.sleep(2)
    webbrowser.open(f"http://localhost:{PORT}")


def main():
    init_db()
    # 加载 MacCMS 源配置
    config_path = os.path.join(os.path.dirname(__file__), "data", "maccms_sources.json")
    if os.path.exists(config_path):
        load_sources(config_path)
        from app.maccms_source import get_manager
        count = len(get_manager().get_all())
        logger.info(f"已加载 {count} 个 MacCMS 视频源")
    # 启动爬虫定时器（后台）
    start_crawler_scheduler()
    # 启动浏览器（后台）
    threading.Thread(target=open_browser, daemon=True).start()
    # 启动服务
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
