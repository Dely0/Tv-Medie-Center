# TV Media Center

家庭用免费观影软件，免会员、免广告。在 miniPC / 电脑上运行，通过遥控器操作，在电视上观看电影、电视剧、综艺、动漫。

## 快速开始

### 在新电脑上安装

1. **安装 Python** — 访问 https://www.python.org/downloads/ 下载安装（勾选 Add Python to PATH）
2. **安装依赖** — 打开命令行进入程序目录执行：`pip install fastapi uvicorn`
3. **启动** — 双击 `start-tv.bat`
4. **（可选）开机自启** — 双击 `install-startup.bat`
5. **（可选）遥控器回退键修复** — 双击 `install-ahk-remap.bat`

## 操作方式

| 按键 | 功能 |
|------|------|
| ↑ ↓ ← → | 导航菜单 / 切换分类 / 选择视频 |
| Enter | 确认 / 播放 |
| Esc | 退出全屏 → 停止播放 → 返回首页 |
| F / 设置键 | 呼出搜索 |

### 搜索

1. 按 **F** 呼出搜索框
2. 输入关键词，自动搜索
3. 按 **↓** 进入结果列表，方向键浏览
4. 按 **Enter** 进入视频详情
5. 按 **Esc** 关闭搜索

### 播放

- 在详情页点击播放按钮或选择剧集
- 自动全屏，支持进度条拖拽
- 上一集 / 下一集切换，播放完自动连播
- Esc 退出全屏，再按 Esc 关闭播放回到详情

### 历史记录

导航栏点击"历史记录"查看看过的所有视频。

## 文件一览

| 文件 | 用途 |
|------|------|
| `start-tv.bat` | 一键启动 |
| `restart.bat` | 重启（杀进程+启动） |
| `clear-cache.bat` | 清 Edge 缓存后重启 |
| `install-startup.bat` | 设为开机自启 |
| `install-ahk-remap.bat` | 修复遥控器回退键（安装 AutoHotkey） |
| `remap-remote.ahk` | 遥控器映射脚本（需 AHK） |
| `data/maccms_sources.json` | 视频源配置 |
| `config.py` | 端口等设置（默认 8080） |

## 视频源

预置 3 个 MacCMS 资源站：
- 量子资源（约 14 万部）
- 光速资源（约 11 万部）
- 最大资源（约 12 万部）

源失效时修改 `data/maccms_sources.json` 中的 `base_url`，搜索"TVBox 接口"找最新源。

## 遥控器适配

如果回退键会导致离开页面：

1. 双击 `install-ahk-remap.bat`（自动下载 AutoHotkey + 配置映射）
2. 或手动下载安装 https://www.autohotkey.com/ 后双击 `remap-remote.ahk`

这会将回退键映射为 Esc，不影响键盘。
