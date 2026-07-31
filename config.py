"""全局配置"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "media.db")

# 服务端口
PORT = 8080

# 爬虫定时（秒），默认每6小时
CRAWL_INTERVAL = 6 * 3600

# 搜索缓存有效时间（秒），默认10分钟
SEARCH_CACHE_TTL = 600

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 搜索超时（秒）— 交互式搜索用更短的超时
SEARCH_TIMEOUT = 5

# 用户代理轮换
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
