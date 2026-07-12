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

# 用户代理轮换
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# mpv 路径
MPV_PATH = os.path.join(BASE_DIR, "data", "mpv.exe")
if not os.path.exists(MPV_PATH):
    MPV_PATH = "mpv"

# mpv 参数 — 全屏播放 + 进度条 + 时间显示
MPV_ARGS = [
    "--fullscreen",
    "--vo=gpu-next",
    "--hwdec=yes",
    "--keep-open=yes",
    "--cache=yes",
    "--cache-secs=120",
    "--cache-pause=no",
    "--demuxer-max-bytes=300M",
    "--demuxer-max-back-bytes=100M",
    "--no-ytdl",
    # 屏幕进度条: 始终显示 + 可拖拽 + 显示当前/总时长
    "--osc=yes",
    "--script-opts=osc-visibility=always",
    # 进度信息: 拖拽进度条时弹出时间气泡
    "--osd-level=2",
    "--osd-font-size=32",
    "--osd-on-seek=msg-bar",
    # 窗口标题 (用于焦点获取)
    "--title=TV Media Center",
]
