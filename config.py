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

# 远程视频源配置（快速换源：应用启动/手动触发时拉取并热加载）
REMOTE_SOURCES_URL = "https://raw.githubusercontent.com/Dely0/Tv-Medie-Center/main/data/maccms_sources.json"

# 用户代理轮换
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# ============ 阶段 A：源稳定性 ============

# DoH 解析：系统 DNS 解析失败时回退到 DoH（防 DNS 污染）
DOH_ENABLED = True

# 源健康状态文件（周期健康检查/自动隔离）
SOURCE_HEALTH_FILE = os.path.join(DATA_DIR, "source_health.json")
SOURCE_HEALTH_INTERVAL = 6 * 3600  # 健康检查周期（秒）
SOURCE_HEALTH_DEAD_AFTER = 3       # 连续失败多少次判定为失效并隔离

# 社区源配置（自动从 TVBox 订阅提取 MacCMS 源，独立于远程主配置，防止被覆盖）
COMMUNITY_SOURCES_FILE = os.path.join(DATA_DIR, "maccms_community.json")
TVBOX_SUBSCRIPTIONS = [
    "http://fty.xxooo.cf/tv",
    "http://www.xn--7blz1a99f.cc/tv",
    "https://gh-proxy.net/https://raw.githubusercontent.com/yoursmile66/TVBox/refs/heads/main/XC.json",
]
TVBOX_SYNC_INTERVAL = 24 * 3600  # 订阅同步周期（秒）

# 解析器配置（jx 解析源，从 TVBox 配置 parses 提取或手动维护）
PARSE_SOURCES_FILE = os.path.join(DATA_DIR, "parse_sources.json")

# 播放链（play-lines）
PLAY_LINES_LIMIT = 6          # 最多返回多少条候选线路
PLAY_LINES_MEASURE_TIMEOUT = 8  # 播放链测速总超时（秒）

# 本地代理
HLS_PROXY_MAX_CACHE_MB = 512  # HLS 分片缓存上限（预留，当前实现为透传）
HLS_PROXY_TIMEOUT = 20        # 代理请求超时（秒）

# ============ 阶段 B：drpyS JS 爬虫生态 ============

DRPYS_ENABLED = True
DRPYS_BASE_URL = "http://127.0.0.1:5757"   # drpyS 默认端口（本地服务）
DRPYS_API_PWD = "dzyyds"                    # drpyS 接口密码（.env.development 默认值）
DRPYS_CONFIG_TTL = 600                      # 源配置缓存（秒）
DRPYS_SEARCH_TIMEOUT = 8                    # 单源搜索超时（秒）
DRPYS_CRAWL_PAGES = 3                       # drpy 源每个分类轻量回填页数

SOURCE_REGISTRY_FILE = os.path.join(DATA_DIR, "source_registry.json")
DRPY_ADULT_NAMES_FILE = os.path.join(DATA_DIR, "drpy_adult_sources.json")

# 侧车目录（Node/drpys 均装在 D 盘项目内，避免占用 C 盘）
SIDECAR_DIR = os.path.join(BASE_DIR, "sidecar")
DRPYS_DIR = os.path.join(SIDECAR_DIR, "drpys")
NODE_DIR = os.path.join(SIDECAR_DIR, "node")
NPM_CACHE_DIR = os.path.join(SIDECAR_DIR, "npm-cache")
DRPYS_LOG_DIR = os.path.join(SIDECAR_DIR, "logs")
