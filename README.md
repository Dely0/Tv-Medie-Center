# TV Media Center v1.0.0

家庭电视媒体中心 — 在电脑上连接电视当机顶盒使用的免费观影软件。

## 功能特性

- 🎬 **多源聚合** — 支持 MacCMS V10 标准 API，一个适配器接入数千个视频站
- 🔍 **智能搜索** — 本地缓存 + 远程源并行搜索，双通道 API 协议兼容
- 📺 **电视大屏** — 全屏 Edge Kiosk 模式，遥控器/键盘方向键完全操控
- 🔄 **自动连播** — 当前集播完自动跳转下一集
- 📜 **观看历史** — 自动保存进度，按视频去重，只保留最新记录
- 🧩 **剧集按需加载** — 点击播放时自动从远程源拉取完整剧集列表

## 快速开始

### 下载安装

```bash
git clone https://github.com/Dely0/Tv-Medie-Center.git
cd Tv-Medie-Center
pip install -r requirements.txt
```

### 启动

双击 `restart.bat`，或命令行：

```bash
python main.py
```

自动打开 Edge 全屏窗口访问 `http://localhost:8080`。

### 开机自启

双击 `install-startup.bat`，创建开机启动项。

## 键盘/遥控器操作

| 按键 | 当前焦点 | 行为 |
|------|---------|------|
| ← → | 顶部导航栏 | 切换Tab（首页/电影/电视剧/综艺/动漫/历史记录） |
| ← → | 内容区 | 网格导航选择卡片/剧集 |
| ← → | 播放器按钮 | 导航上一集/下一集/关闭按钮 |
| ← → | 播放器视频 | 快进/快退 10 秒 |
| ↑ | 内容区顶部行 | 回到导航栏当前Tab |
| ↑ | 导航栏 | 无操作（已在顶部） |
| ↓ | 导航栏 | 进入内容区第一个元素 |
| ↓ | 内容区 | 继续向下移动 |
| Enter | 任意焦点 | 确认/播放 |
| Esc | 播放器全屏 | 退出全屏 |
| Esc | 播放器非全屏 | 停止播放，返回详情 |
| Esc | 详情页 | 返回首页 |
| F | 任意页面 | 打开搜索 |

## 添加视频源

编辑 `data/maccms_sources.json`：

```json
{
  "sources": [
    {
      "name": "源名称",
      "base_url": "https://视频站域名",
      "enabled": true,
      "category_map": {
        "movie": "1",
        "tv": "2",
        "variety": "3",
        "anime": "4"
      }
    }
  ]
}
```

验证源是否可用：在浏览器打开 `{base_url}/api.php/provide/vod?ac=list&at=json&pagesize=5`，返回 JSON 即有效。

## 技术栈

- **后端**: Python / FastAPI / SQLite + FTS5
- **前端**: 原生 JS / HLS.js / TV 大屏 CSS
- **爬虫**: MacCMS V10 聚合 API / cloudscraper
- **播放器**: 浏览器内嵌 HLS.js 播放 m3u8 流

## 项目结构

```
Tv-Medie-Center/
├── main.py                 # 入口
├── config.py               # 全局配置
├── VERSION                 # 版本号
├── restart.bat             # 重启脚本
├── install-startup.bat     # 开机自启安装
├── app/
│   ├── api.py              # FastAPI 路由
│   ├── crawler.py          # 爬虫引擎
│   ├── database.py         # SQLite + FTS5
│   ├── maccms_source.py    # MacCMS API 适配器
│   ├── sources.py          # HTML 视频源基类
│   ├── player.py           # 播放器状态管理
│   └── static/
│       ├── index.html      # 单页应用
│       ├── css/tv.css      # TV 大屏样式
│       └── js/app.js       # 前端逻辑
└── data/
    ├── maccms_sources.json # 视频源配置
    └── media.db            # SQLite 数据库
```
