# 视频源稳定性改造计划（A + B + C 一次性完成）

> 版本：2026-08-01（v1.3.0 发布后）
> 目标：从"手工维护 MacCMS 清单"升级为"订阅 TVBox 社区源生态 + 播放自动换源链 + 网络韧性 + 运维自愈"。
> 部署目标：约 200 RMB 的轻量 miniPC（Windows），安全性不敏感（用户已确认）。

---

## 1. 研究结论摘要（为什么这么做）

### 1.1 参考对象拆解

| 对象 | 关键发现 | 可借鉴点 |
|---|---|---|
| 荐片播放器（本地逆向） | WKE 网页壳 + 远程服务端聚合 + 西瓜 P2P SDK + DoH（阿里/Cloudflare/OpenDNS） | DoH 抗 DNS 污染；内容服务端化；P2P 依赖厂商 SDK，不采纳 |
| 饭太硬导航站 | 不是单个站点，是 TVBox 生态入口；配置 2026-08-01 实测 49 个源全部为 JAR 爬虫（csp_*），含玩偶网、荐片、网盘搜索等 | 支持 TVBox 爬虫协议即可复用整个生态 |
| 玩偶网 wogg.net | 本身是 MacCMS 站，但 API 直连 403（UA/防盗链反爬） | 裸 API 源会越来越少，必须支持爬虫/伪装 |
| FongMi/TV | 播放失败自动换源链：解析器 → 线路 → 搜索其他站 → 下一站点；JS/JAR/Python 三引擎；WebView 嗅探；DoH/hosts/CORS 注入 | 换源链与嗅探是核心设计 |
| drpyS（drpy-node） | Node 服务端实现 TVBox JS 爬虫协议，自带 100+ 源（含荐片.js）、/parse 解析接口、代理/媒体代理，持续更新 | 可作为本项目的 JS 爬虫侧车，浏览器端天然契合 |
| alist-tvbox | Java 后端：TVBox 订阅聚合 + 网盘（夸克/UC/阿里/115）+ Emby/Jellyfin/B站/YouTube + Python 爬虫插件 | 网盘源与多后端聚合的参照，可做可选侧车 |
| atv-player | 本地 HLS 代理（m3u8 重写、防盗链头透传、TS 分片缓存）、多线路播放列表自动连播 | HLS 代理设计直接可用 |

### 1.2 现状缺口（对应改造点）

1. 源类型单一：只支持 MacCMS JSON API，接不了爬虫型源（玩偶/荐片/网盘）。
2. 换源是"事后查询"：播放失败才查 `/alternates`；需要改为"播放前预取线路、失败无感切换"。
3. 无解析器/嗅探：play 地址为 HTML 播放页时无法取直链。
4. 网络层裸奔：无 DoH、无按源 UA/Referer/Origin 配置，部分源是被反爬拦而非真死。
5. 单点订阅：社区配置地址经常 502（实测 ok213/qiaoji8/mitvbox 已挂），必须多订阅 + 自动合并。
6. 源运维靠手工：缺周期健康检查、自动隔离/恢复、优先级动态调整。

---

## 2. 目标架构

```
┌─────────────────────────── TV 前端（Edge 全屏 + hls.js）──────────────────────────┐
│ 首页/分类/搜索/详情/播放链/线路切换/OSD 网速/源状态诊断                              │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                │ REST API
┌───────────────▼─────────────── FastAPI 后端（main.py / app/api.py）───────────────┐
│ app/source_framework/  统一源注册表 + 适配层 + 归一化 + 去重 + 成人过滤               │
│   ├─ maccms_adapter    现有 MacCMS 源（type 0/1）                                  │
│   ├─ drpy_adapter      drpyS 侧车 HTTP 客户端（type 3 JS 源）                       │
│   ├─ jar_adapter       Java 侧车客户端（type 3 JAR 源，csp_*）【阶段 C】             │
│   ├─ parse_adapter     解析器 URL + 正则嗅探（m3u8/mp4 抽取）                       │
│   └─ tvbox_config      TVBox JSON 配置解析/多订阅合并/版本保护                        │
│ app/ops/              健康检查 / 自动隔离恢复 / 订阅同步 / 源状态持久化              │
│ app/net/              DoH 解析 / 按源请求头 / HLS+媒体本地代理                      │
│ 现有：database / crawler / douban / adult / source_selector / source_updater        │
└──────┬──────────────────────┬───────────────────────┬─────────────────────────────┘
       │ HTTP                  │ HTTP                   │ 子进程/HTTP
┌──────▼───────┐   ┌───────────▼──────────┐   ┌─────────▼───────────┐
│ drpyS 侧车    │   │ Java JAR 侧车        │   │ 社区 TVBox 配置源    │
│ (Node, 阶段B) │   │ (JRE, 阶段C)         │   │ 饭太硬/OK/其他多订阅  │
└──────────────┘   └──────────────────────┘   └─────────────────────┘
```

### 2.1 统一适配层接口（新源接入不改上层）

所有适配器实现同一接口，返回统一归一化结构：

```python
class SourceAdapter:
    key: str            # 唯一源标识（如 csp_WoGGGuard / drpy_荐片 / maccms_量子资源）
    name: str
    source_type: str    # "maccms" | "drpy" | "jar" | "parse"
    enabled: bool
    health: HealthState # ok / slow / dead / untested

    async def search(keyword) -> list[NormalizedVideo]
    async def list_category(category, page, filters) -> list[NormalizedVideo]
    async def detail(video_key) -> NormalizedVideo + episodes
    async def play(video_key, ep_index) -> PlayCandidate | None
```

播放候选统一为：

```python
PlayCandidate:
    source_key / source_name
    play_url            # 直链 m3u8/mp4/flv 或"待解析页面 URL"
    kind                # direct | page | parse
    headers             # {ua, referer, origin}（防盗链）
    speed_score         # 最近测速分（0-100）
    bitrate             # 最高码率（解析 master 时得到）
    resolvable_by       # 本候选由哪个适配器/解析器产出
```

### 2.2 数据流

- **搜索/分类/首页**：SourceRegistry 并行扇出（限并发 ≤8、单源超时 5-15s）→ 归一化 → 标题去重 → **成人过滤管线**（adult.py 统一入口）→ 落库/返回。
- **播放**：`/api/video/{id}/play-lines` 并行解析全部可用源 → 测速排序（复用 source_selector）→ 返回线路列表；前端 hls.js 逐条 failover。
- **每日任务**：订阅刷新 → 健康检查 → 源优先级/隔离状态更新 → 推荐池/豆瓣榜重建（现有调度器扩展）。

---

## 3. 阶段 A：纯 Python 增强（无新运行时，先止血）

> 目标：现有架构下把"换源"做成"预取多线路 + 无感 failover"，并让源池具备自愈能力。

### A1. 播放链接口 + 前端自动切线路

- 新增 `GET /api/video/{id}/play-lines?episode=N`：
  - 并行解析当前视频在全部启用源中的播放地址（DB 已有同标题记录直接取，缺的按标题查源）；
  - 对直链做首分片测速（复用 `measure_source`），HTML 页候选交给解析器/嗅探（A6 前置部分）；
  - 按 `speed_score` 排序，最多返回 8 条，缓存 10 分钟；
  - 返回每条候选的 `headers`（UA/Referer）供前端直连或走后端代理。
- 前端改造（`app/static/js/app.js`）：
  - `loadAndPlayUrl` 升级为加载线路列表；
  - HLS `ERROR` / 缓冲超时（当前 20s，降到 8s 无进度）/ `FRAG_LOADED` 长时间无字节 → 自动切下一条线路并 OSD 提示"正在切换线路：xx";
  - 保留现有 `/alternates`、`/best-source` 作为兜底；
  - OSD 增加"线路"指示与手动切换（左/右键选择，Enter 确认）。
- 后端记录每次线路切换结果（成功/失败/耗时），供健康检查与排序使用。

### A2. 源健康检查与自动隔离

- 新增 `app/ops/health.py` + `data/source_status.json`（或 DB 表）：
  - 检查项：API 首页拉取（延时）、分类首页、固定关键词搜索、播放地址首分片速度；
  - 连续 3 次失败 → `dead` 自动隔离（从搜索/分类/播放候选剔除，保留配置）；
  - 隔离源定时重探（每 6 小时），通过一次即恢复；
  - `slow` 状态降优先级（排序靠后）。
- `/api/sources`、`/api/maccms/sources` 增加健康字段；新增 `POST /api/ops/run-health-check`、`GET /api/ops/status`。

### A3. MacCMS 源池扩充（从社区配置自动提取）

- `app/source_framework/tvbox_config.py`：解析 TVBox JSON 配置（`sites[].type 0/1` 即 MacCMS XML/JSON），提取 `{name, api(base_url), category_map}` 并入现有 `maccms_sources.json` 管理；
- `app/ops/sync.py` 多订阅地址（饭太硬主/备用、OK影视、巧技、南风等，从饭太硬导航页解析），每日拉取合并去重，沿用版本号+源数防旧覆盖；
- 扩充源按 A2 健康检查自动启用/停用，不再手工逐个验证。

### A4. DoH 解析 + 按源请求头

- `app/net/doh.py`：解析失败或源域名被污染时走 DoH（阿里 `dns.alidns.com/resolve`、Cloudflare `dns-query`），带 TTL 缓存；默认开启，可配置关闭。
- `app/net/headers.py`：每个源可配置 `ua / referer / origin / cookie` 模板，所有适配器统一应用（解决 wogg 这类 403）。

### A5. HLS / 媒体本地代理

- `GET /api/hls-proxy?url=...&ref=...&ua=...`：
  - 以源 headers 拉取 master/media m3u8，重写分片地址为代理地址（相对路径转绝对）；
  - 透传 Referer/UA，响应加 CORS 头；
  - 可选 TS 分片 LRU 缓存（临时目录，上限 ~512MB，可配置关闭）。
- `GET /api/media-proxy?url=...`：mp4/flv 的 Range 直传代理（解决防盗链 + 前端直连跨域）。
- 前端策略：带 headers 或遇到跨域/403 的候选自动走代理。

### A6. 解析器与嗅探（Python 版，阶段 A 先做基础版）

- `app/source_framework/parse.py`：
  - `parse_url` 解析：调用配置的解析器（如 `https://jx.aidouer.net/?url=`），从返回页/302 中提取直链；
  - 嗅探：对 HTML 播放页抓取 `<video src>`、`window.m3u8`、正则匹配 `https?://...\.m3u8` / `\.mp4` 等，支持 UA/Referer 注入；
  - 结果进入 play-lines，标记 `kind=parse`。

### 阶段 A 验收

- 任一视频播放失败/卡顿 8s 内自动切到可用线路，OSD 可见；
- 失效源一个健康检查周期内自动隔离，首页/搜索/播放候选不再出现；
- 新增 MacCMS 源只需改订阅 JSON，无需改代码；
- 无新运行时依赖，原部署脚本不变。

---

## 4. 阶段 B：接入 TVBox JS 爬虫生态（drpyS 侧车）

> 目标：获得 drpy 生态 100+ 源（含荐片、光影、立播、网盘搜索等），并统一走播放链。

### B1. drpyS 侧车管理

- `sidecar/drpys/`：固定版本（当前 main 2026-03，V1.4.x），`install.bat`（检测 Node ≥18，缺失则下载便携版）、`start.bat`、`stop.bat`、`update.bat`（git pull + 依赖更新，可选每日）；
- 端口固定 4568（避免冲突），关闭远程访问（仅 127.0.0.1），健康端点 `/health`；
- `main.py` 启动前探测侧车，未启动自动拉起；退出不主动杀（独立服务），`restart.bat` 统一管理。

### B2. drpy 适配器

- `app/source_framework/drpy_adapter.py`：调用 drpyS 标准接口：
  - 分类/首页：`GET /api/{module}?ac=detail`（或 `ac=list`），解析 `class` / `list`；
  - 详情：`ac=detail&ids=...`；
  - 搜索：`wd=...&ac=detail`（注意 drpyS 搜索返回结构与 detail 一致）；
  - 播放：`play=...`（lazy 解析），返回 `url` + 播放器类型；
  - 代理资源（图片/流）：走 drpyS `/proxy/{module}/` 或本后端转发。
- 归一化到统一结构，成人过滤、去重、落库流程与现有源完全一致。

### B3. JS 源订阅与启用

- 从 drpyS 自带 `spider/` 目录 + 社区源仓库（hjdhnx/drpy2 源列表、饭太硬配置中 `xxx.js` 类型的源）生成可启用清单；
- `data/source_registry.json`：每个源 `enabled / adult / category_map / health` 可配；
- 默认只启用实测可用的影视源，直播/音乐/教育类源默认关闭（可在配置开启）；
- 成人源（如 drpy 中的成人规则）默认关闭并走成人隔离逻辑。

### B4. 解析器/嗅探增强

- 接入 drpyS `/parse/:jx` 与自有 `parse.py` 双通道；
- 播放候选解析失败时，尝试"其他站搜索同名 → 下一站点"（FongMi 换源链）：
  `线路解析失败 → 换同源其他线路 → 换其他源 → 换其他站点搜索结果`。

### B5. 首页/推荐/历史集成

- 新源内容进入爬虫回填与每日推荐池（现有 crawler/douban 逻辑扩展）；
- 历史记录、继续观看、猜你喜欢全部走统一成人过滤，行为与现有源一致。

### 阶段 B 验收

- 荐片、光影等 drpy 源可搜索/分类/播放，且能进播放链自动 failover；
- 成人内容在新源同样隔离（开关关闭时搜索/首页/猜你喜欢 0 条）；
- 侧车崩溃自动重启，后端无感知。

---

## 5. 阶段 C：JAR 爬虫宿主 + 饭太硬全量源 + 可选网盘

> 目标：解锁饭太硬 49 个 JAR 源（csp_*Guard，含玩偶/荐片官方爬虫），并补齐部署与网盘能力。

### C1. Java JAR 侧车

- `sidecar/jarhost/`：便携 JRE（Temurin 17/21，约 40MB）+ `JarHost` 服务：
  - 读取任务（`spider.jar` URL + md5 + 类名 + ext + 调用参数）；
  - 下载并校验 md5（饭太硬配置格式：`url;md5;hash`）；
  - 以 `URLClassLoader` 加载，调用 `homeContent / categoryContent / detailContent / searchContent / playerContent`（协议见 `docs/reference_SPIDER_API.md`）；
  - JSON-RPC（本地 127.0.0.1:4569）或子进程 stdin/stdout 通信；超时 20s；
  - 单例复用已加载 JAR（hash 变化才重载），限制结果大小防异常。
- 安全：用户明确不敏感；仍提供可选的"低权限账户 + 仅允许目标站点网络"开关。

### C2. 饭太硬配置接入

- `tvbox_config.py` 完整支持 `type 3 + api=csp_*`（JAR）与 `type 3 + api=*.js`（转 B 通道）以及 `spider`（默认 JAR）；
- 接入实测：分类/搜索/详情/播放逐源跑基准（复用 `scripts/benchmark_sources.py` 思路），按 连接速度/数据完备度 决定默认启用清单；
- 玩偶网、荐片等重头源进入统一播放链；网盘搜索类源（夸克/UC/阿里）如可用则保留，需要登录态的单独配置项。

### C3. 网盘源（可选，评估后决定）

- 方案一：alist-tvbox 侧车（Java jar 或 Docker），提供 TVBox 聚合接口，适配器直连；
- 方案二：drpy 网盘源（已有 `spider/catvod/alist.js`、盘搜源），配合 AList 登录配置；
- 需要用户提供至少一个网盘账号/分享配置，工作量大，作为 C 阶段最后项，默认先不做，留接口。

### C4. 一键部署与便携打包

- `install-all.bat`：检测/安装 Python 依赖、Node、JRE，初始化侧车；
- `start-all.bat`：按序拉起 drpyS → jarhost → 主服务 → Edge 全屏；
- `restart.bat` 统一管理全部进程；
- `scripts/build_portable.py`：产出单目录便携包（Python embeddable + 依赖 + Node + JRE + 项目代码），拷贝即用；
- 源订阅配置 UI：现有诊断面板增加"源管理"（列表/启用停用/立即健康检查/手动同步配置）。

### 阶段 C 验收

- 饭太硬 49 源中可用源自动进入播放链，玩偶/荐片可正常播放；
- 任一源失效无需人工干预，健康检查自动隔离，其他源自动顶替；
- 全新部署流程 ≤10 分钟（拷贝 → install-all → start-all）；
- 重启后全部服务自愈。

---

## 6. 跨阶段通用项

### 6.1 成人内容隔离（必须贯穿全部阶段）

- `app/adult.py` 保持唯一判定入口：`is_adult(source_name, title) -> bool`；
- 新适配器（drpy/jar/parse）产出数据必须过同一管线；
- 成人源名单增加新源的 `source_key`；配置关闭时新源成人内容同样不可见；
- 搜索条件化逻辑对全部源生效。

### 6.2 性能预算（200 RMB miniPC）

- 搜索并发 ≤8、单源超时 5-15s；
- 播放候选解析并发 ≤6、总超时 ≤10s；
- 测速缓存 10 分钟、播放链缓存 10 分钟、健康检查 6 小时周期；
- HLS 分片缓存上限 512MB，可配置；
- 每日同步任务放后台线程，不阻塞首页（沿用现有架构）。

### 6.3 提交与版本节奏

- 每个阶段完成：自测 → 提交 → push → 打 tag（v1.4.0 / v1.5.0 / v1.6.0）；
- 每个阶段结束后更新 `SOURCE_GUIDE.md` 与 `README.md`。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 社区配置地址失效（常态） | 多订阅 + 自动合并 + 健康检查隔离，配置源本身可热更新 |
| 爬虫协议/站点改版 | 适配层隔离，单源失效不影响整体；FongMi/社区持续跟进 |
| JAR/JS 为第三方代码 | 用户已确认安全性不敏感；仅用可信源（饭太硬/OK），可开关 |
| 源返回内容质量差（假源/广告页） | 嗅探白名单正则 + 播放候选实测（首分片必须可下载） |
| miniPC 性能不足 | 并发/超时/缓存预算约束；侧车轻量（Node/JRE 各几十 MB） |
| 网盘源需登录态 | 做成可选配置，默认关闭，失败不影响其他源 |

---

## 8. 里程碑

| 里程碑 | 内容 | 产出版本 |
|---|---|---|
| M1 | 阶段 A（播放链/健康检查/源池扩充/DoH/代理） | v1.4.0 |
| M2 | 阶段 B（drpyS + JS 源生态） | v1.5.0 |
| M3 | 阶段 C（JAR 宿主 + 饭太硬全量 + 便携部署） | v1.6.0 |

每阶段之间安排用户实测反馈（家庭成员使用），再进入下一阶段。

### 阶段 C 调整记录（2026-08-02）

- 饭太硬 JAR 爬虫经实测为 Android DEX + ARM 原生 .so 加固（ftyguard_v7/v8），
  无法在 Windows miniPC 的 JVM 上运行，**JAR 宿主方案取消**。
- 替代方案已实施：drpyS 采集站清单自动并入（90+ 端点实测 58 个可用，
  过滤成人/去重/测通后入池，社区源达 45 个）；便携打包脚本
  `scripts/build_portable.py`；网盘源保留接口但需登录凭据，默认关闭。
