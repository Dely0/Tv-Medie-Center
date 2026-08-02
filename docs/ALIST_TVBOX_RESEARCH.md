# alist-tvbox（第一项方案）调研报告

> 调研日期：2026-08-02
> 背景：drpyS 已升级至 1.4.3 并完成源注册表刷新（288 源 / 启用 112 / 成人 6）。
> 更新后实测：玩偶哥哥[盘]、玩偶哥哥DM[盘]、荐片[优]、雷鲸小站[盘] 等健康检查恢复 OK
> （升级前为 HTTP 500）。但仍需评估更稳定的网盘/聚合源方案，本报告聚焦 alist-tvbox。

---

## 一、结论速览

| 项目 | 结论 |
|---|---|
| 可行性 | **可行**。独立版 JAR 官方发布，Java 21 直接运行；核心能力是"云盘聚合 + TVBox 订阅代理" |
| 活跃度 | 高。`power721/alist-tvbox` 3058 star，最近一次发布 1.36.0（2026-08-01），几乎每日发版 |
| 部署形态 | 独立版 = `alist-tvbox-1.0.jar`（115MB）+ AList（Windows 版 ~43MB）+ JRE 21 |
| 夸克支持 | 支持。AList 夸克驱动 + Cookie；另有 `/api/play/quark`、`/api/proxy/quark/{id}` 专用播放/代理接口 |
| 与我们现有架构兼容性 | 高。它暴露标准 TVBox HTTP 协议 `/vod`，和现有 MacCMS 适配器几乎同构，可新增一个 adapter 接入统一源框架 |
| Windows 支持 | 官方文档面向 Linux/Docker，Windows 裸 JVM 非官方支持（有 open issue），**需要试点验证**；退路是 Windows Docker Desktop / WSL2 |
| 资源占用 | 磁盘约 250~350MB（D 盘充足）；内存建议 512MB~1GB（Java）+ 100~200MB（AList），200 元 miniPC 可承受但需实测 |

---

## 二、项目概况

- 仓库：https://github.com/power721/alist-tvbox （3.0k star / 574 fork，2026-08-01 仍有提交）
- 定位：AList proxy server for TvBox。媒体聚合（AList/Emby/Jellyfin/飞牛/B 站/YouTube/直播）+ 云盘管理
  （阿里、百度、夸克、UC、115、123、天翼、迅雷、PikPak 等）+ TvBox 订阅系统 + Python 爬虫插件 + 本地代理加速。
- 中文文档：`doc/README_zh.md`，完整 REST API：`api.md`（45 个 Controller / 350+ 端点）。

## 三、发布与部署形态

### Releases（官方发布，非源码自建）

| 资产 | 大小 | 说明 |
|---|---|---|
| `alist-tvbox-1.0.jar` | 115.3MB | Spring Boot fat jar，`java -jar` 直接运行（需 Java 21） |
| `alist-tvbox-1.0.jar.original` | 3.2MB | 未打依赖的原始 jar（用于加载 csp_* 蜘蛛类） |
| `atv.tar.gz` | 88.7MB | Docker 镜像包（haroldli/alist-tvbox） |

最新版本：1.36.0（2026-08-01 发布），下载地址：
`https://github.com/power721/alist-tvbox/releases/download/1.36.0/alist-tvbox-1.0.jar`

### 运行组成

```
alist-tvbox (端口 4567)   ← 管理后台 + TVBox HTTP API (/vod, /sub/{id}, /play, /p/{token}/{id})
        │ 通过 /api/alist/start|stop|restart 管理
AList (端口 5244)         ← 云盘驱动（夸克 Cookie 等）+ 文件搜索/直链
```

- jar 默认连接 `http://127.0.0.1:5244` 的 AList（`application.yaml` 中 `app.sites[0].url`）。
- Docker 镜像内置 AList 二进制；**裸 jar 模式需要自行部署 AList**（Windows 版 AList 官方发布：
  `alist-windows-amd64.zip` ~43MB，最新 v3.62.0 / 仓库配置标注 v4.1.1，需对齐版本）。
- 管理后台默认账号 admin/admin，订阅地址 `http://ip:4567/sub/0`。
- 数据目录：Docker 下为 `/data/atv`、`/opt/atv`；Windows 裸跑需要把 Spring 配置
  （`-Dspring.config.additional-location`）和数据路径指到项目 D 盘目录。

### Windows 支持情况（重点风险）

- 官方 README 只给 Linux/Docker 安装方式；GitHub issue #291（"如果支持Windows就好了"）仍为 open，
  说明**裸 JVM 跑 Windows 不是官方承诺场景**，但 Java 应用本身跨平台，主要障碍是路径假设与 AList 进程管理。
- 有使用者通过 Windows Docker Desktop 部署成功（issue #291 评论）。
- 结论：先在本开发机做 1~2 小时试点（装 JRE21 + 下载 jar + java -jar），能起来即可继续；不行则退 Docker Desktop/WSL2。

## 四、TVBox 订阅站点类型确认（回答上次遗留问题）

`/sub/{id}` 返回的站点是**混合类型**：

| 类型 | 示例 | 说明 |
|---|---|---|
| type 3 + api=`/cat/*.js` | 快看、云盘4K（`doc/my.json`） | JS 蜘蛛，由 TVBox 客户端 JS 引擎执行 |
| type 3 + api=`csp_*` + jar=`ATV_ADDRESS/spring.jar` | csp_Bili、csp_PyProxy | JAR 类蜘蛛，由 TVBox 客户端从服务器 jar 加载 |
| 内置站点 | AList（本地/公共 AList）、Emby、Jellyfin、飞牛、B 站、直播 | 由 alist-tvbox 服务端实现 |

**对我们的意义**：我们不需要模拟 TVBox 客户端去解析 JS/JAR 站点。alist-tvbox 自身把全部能力
（AList、Emby、Jellyfin、飞牛、B 站、云盘搜索等）暴露为 `/vod` 标准 HTTP 协议
（`ac=detail/list` + `wd/ids/t/playIndex`），返回 `vod_name/vod_pic/vod_play_url` 格式——
与我们现有 MacCMS 适配器几乎同构，新增一个 adapter 即可接入，无需 JAR 宿主。

## 五、夸克/网盘能力（对应"没有网盘资源"痛点）

- AList 驱动支持：夸克、UC、百度、阿里、115、123、天翼、迅雷、PikPak 等；夸克用 Cookie（用户已提供）。
- alist-tvbox 专属接口：
  - `POST /api/play/quark` 夸克播放解析
  - `GET /api/proxy/quark/{id}` 夸克视频流代理
  - 分享链接导入/管理、盘搜（`/pansou`）、链接有效性检测
  - 本地代理加速：`local_proxy_config`（并发、分块大小），可缓解网盘播放卡顿
- 解决路径：AList 挂载用户自己的夸克存储 + 导入分享/索引后，`/vod` 搜索/详情/播放才有真实数据；
  公共 AList（神族九帝、姬路白雪等）可作补充，但不稳定，不宜当主力。

## 六、与现有项目集成方案

### 方案 1（推荐）：alist-tvbox 作为聚合源侧车

1. `sidecar/alist-tvbox/` 固定版本部署：JRE21（D 盘）+ `alist-tvbox-1.0.jar` + AList Windows 版；
   端口 4567/5244，仅绑定 127.0.0.1；`main.py` 启动前拉起（复用 `app/sidecar/drpys` 模式），
   升级脚本仿照 `update_drpys.ps1`（保留 data 目录）。
2. 新增 `app/source_framework/alist_tvbox_adapter.py`：
   - `search`：`GET /vod?ac=detail&wd=关键词`
   - `list_category`：`GET /vod?ac=detail&t=分类&pg=N`
   - `detail`：`GET /vod?ac=detail&ids=视频ID`
   - `play`：`GET /vod?ac=play&ids=视频ID&playIndex=集数` → 直链或 `/p/{token}/{id}` 代理地址
3. 接入现有机制：统一源注册表、健康检查（`source_health.json`）、优先级排序、播放链
   （`play_lines.py`）、成人过滤（`app/adult.py`）全部复用，输出端统一过 `is_adult`。
4. 播放地址如果带 `127.0.0.1:5244/d/` 或 `/p/` 代理，走现有 hls/media-proxy 透传即可；
   若跨端口受限，加一行本地代理白名单。

### 方案 2：仅部署 AList（不装 alist-tvbox）

- 直接调 AList 自身 API（`/api/fs/list`、`/api/fs/search`、`/d/直链`），夸克 Cookie 同样可用。
- 优点：更轻（少 115MB jar + 少一个服务）；缺点：没有聚合索引、分享管理、代理加速、
  Emby/Jellyfin/飞牛等媒体源，且需要自己实现夸克分享解析（工作量大）。

### 方案 3：仅把 `/sub` 中 JS 站点导入 drpyS

- 把 `/cat/*.js` 等 JS 蜘蛛下载进 drpyS `spider/` 并注册。只能补充视频站点，**解决不了网盘与缓冲问题**。

### 建议

选方案 1。理由：直接命中用户两个痛点（网盘资源 + 播放缓冲），且复用现有 adapter 框架，
集成工作量集中在"侧车部署 + 一个 adapter + 健康/成人接入"。

## 七、资源占用（miniPC 200 元预算）

| 项 | 大小/占用 | 备注 |
|---|---|---|
| JRE/JDK 21 | ~190MB（可 jlink 精简到 ~80MB） | 清华/华为镜像下载到 D 盘 |
| alist-tvbox jar | 115MB | 固定版本 |
| AList Windows | ~43MB | 固定版本 |
| 内存 | Java 512MB~1GB + AList 100~200MB | 与现有 Python/Node 服务共存需实测；建议 -Xmx768m |
| 端口 | 4567、5244 | 与现有 8080/5757/57570 无冲突 |

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| Windows 裸 JVM 非官方支持 | 先试点 `java -jar`；失败则 Windows Docker Desktop / WSL2（部署复杂度上升） |
| AList 版本与 alist-tvbox 不匹配 | 按 `config/alist.json`（标注 v4.1.1 / API v4）选对应 AList 版本，试点时验证搜索/播放 |
| 夸克风控/限速 | 控制并发、Cookie 续期（AList 支持扫码刷新）；播放代理并发参数可调 |
| 公共 AList 站点不稳 | 只作补充源，主力用用户自己的夸克账户 |
| 成人内容混入 | adapter 输出统一过 `is_adult`（沿用现有过滤），与成人开关联动 |
| 项目发版极快 | 固定版本号 + 升级脚本（仿 update_drpys），不在运行中自动升级 |

## 九、下一步试点计划（先验证再写代码）

1. 下载 Temurin 21（清华镜像）到 `D:\Code\Tv-Medie-Center\sidecar\jre21`（不占 C 盘）。
2. 下载 `alist-tvbox-1.0.jar`（1.36.0）到 `sidecar\alist-tvbox\`。
3. 下载 AList Windows amd64 到 `sidecar\alist\`，按 `config/alist.json` 初始化，填入夸克 Cookie。
4. 启动 AList（5244）→ 启动 jar（4567）→ 验证：
   - `http://127.0.0.1:4567/sub/0` 能出订阅；
   - `/vod?ac=detail&wd=测试` 能搜到网盘资源；
   - `/vod?ac=play&ids=...&playIndex=1` 能出可播放地址（优先走 `/p/` 代理）。
5. 全部通过后，再写 `alist_tvbox_adapter.py` 并接入统一源框架。

---

## 十、实施状态（2026-08-02 已完成）

### 部署（本机已验证）

- `sidecar/jre21/`：OpenJDK 21（华为镜像，~192MB，D 盘）
- `sidecar/alist/`：AList Windows 版（端口 5244，仅 127.0.0.1），数据目录 `sidecar/alist/data`
- `sidecar/alist-tvbox/`：alist-tvbox 1.36.0 jar（端口 4567，仅 127.0.0.1）
- Windows 硬编码路径 `\opt\atv\` 通过目录联接（junction）指向 D 盘 sidecar：
  `D:\opt\atv\{alist,index,log,www,config}`（sidecar 启动时自动创建）
- H2 设置 `enabled_token=false`（首次启动自动修正，TVBox 接口免 token）
- 夸克 Cookie 从 `sidecar/drpys/config/env.json` 自动挂载为 AList `/quark` 存储
  （`use_transcoding_address=true`，夸克文件走转码直链，mkv 也可转 mp4 播放）

### 已实现功能

- `app/sidecar/alist_tvbox.py`：AList + jar 的启动/健康自检/自动拉起，`main.py` 启动时调用
- `app/source_framework/alist_tvbox_adapter.py`：走 TVBox 协议 `/vod`
  - 搜索：`/vod?ac=detail&wd=`（只保留视频文件类结果，过滤 epub/azw3 等书籍）
  - 详情：`/vod?ac=detail&ids=` → 单集（网盘文件）
  - 播放：`/vod?ac=play` → alist-tvbox 本地代理 `/p/{token}/{id}`
- 注册表接入：搜索源列表、按名查找、源状态页均包含 alist-tvbox
- 搜索接口给 alist-tvbox **独立线程**执行（避免排在 100+ 个源后面被整体超时饿死）
- 播放链把 `/p/` 代理地址识别为直接媒体流（不再误判为“播放页待解析”）

### 已验证链路（本机实测）

- `GET /sub/0` 200（订阅聚合）
- `GET /vod?ac=detail&wd=三体` → 60 条（公共 AList：神族九帝/七米蓝）
- `GET /vod?ac=detail&ids=4$108$1` → play_url=`/p/-/4@108`
- `GET /p/-/4@108` → 200，16.5GB 真实 MKV 流
- 本项目 `/api/search?q=阿凡达` → `[阿凡达].Avatar.2009.mkv`（远程入库）
- `/api/video/{id}/play` → `/p/` 地址直出，`use_proxy=false`

### 已知限制

- **公共 AList 稳定性参差**：七米蓝等部分源从国内访问会 302 到国外 CDN（SharePoint）
  导致测速/播放失败；这类线路会被健康机制自然淘汰，夸克本地代理线路不受影响
- **搜索仅覆盖已配置站点**：本地 AList（夸克）+ SITE 表里添加的公共 AList；
  新增站点需写入 alist-tvbox 的 H2 `SITE` 表（见下）
- **mkv 播放依赖浏览器/源站**：Edge 对 mkv 支持有限；夸克文件建议走转码直链（已开启）
- 网盘搜索只返回“视频文件”结果，目录/压缩包/书籍不会进入片库

### 2026-08-02 修复：alist 文件名条目无法匹配常规源线路

**现象**：搜索到的《阿凡达》《流浪地球》等 alist-tvbox 条目（标题为文件名，如
`[阿凡达].Avatar.2009.mkv`）进入播放页后只有 1 条线路且无法播放。

**根因**：alist 条目以网盘文件名入库，`normalize_title()` 生成的是
`阿凡达avatar2009mkv` 这类脏规范化标题，与常规源的 `阿凡达` 永远无法精确匹配。
播放链（`play_lines.py`）的同片 gather 与后台跨源补充都依赖精确匹配，因此
gather 不到本地库中几十个同片常规源，只剩七米蓝指向国外 SharePoint CDN 的
1 条坏线路。

**修复**：
1. `alist_tvbox_adapter.py` 新增 `clean_file_title()`：从文件名清洗出干净标题
   （`[阿凡达].Avatar.2009.mkv` -> `阿凡达`，`[流浪地球1080Px264].mkv` -> `流浪地球`，
   `流浪地球2.Wandering.Earth.2.2023.mkv` -> `流浪地球2`），搜索/详情入库时使用
   干净标题，原文件名保留在 remarks。
2. `play_lines.py` 新增标题变体匹配：alist-tvbox 条目在 gather 本地同片与后台
   跨源补充时，会额外用清洗后的主标题匹配（支持“主标题+国语/粤语/高清”等常见后缀），
   其他源保持原有精确匹配不变。
3. 回填已有 alist-tvbox 记录的 title/title_norm（2 条）。

**验证**：`/api/video/21192/play-lines`（阿凡达）与 `/api/video/21089/play-lines`
（流浪地球）均返回 10 条同片线路，360/暴风/艾旦/荐片等常规源测速可用并排在前面，
alist-tvbox 线路（SharePoint 429/超时）排在最后。

### 新增公共 AList 站点（一次性 H2 操作）

停掉 alist-tvbox jar 后，用 `sidecar/alist-tvbox/h2tmp/h2.jar` 执行（SQL 需 GBK 编码，
中文名避免乱码；SITE.ID 需显式赋值）：

```sql
INSERT INTO SITE (ID, DISABLED, NAME, SEARCHABLE, SORT_ORDER, STORAGE_VERSION, URL, XIAOYA, INDEX_FILE, PASSWORD, TOKEN, FOLDER)
VALUES (3, FALSE, '神族九帝', TRUE, 3, 4, 'https://alist.shenzjd.com', FALSE, NULL, '', '', '');
INSERT INTO SITE (ID, DISABLED, NAME, SEARCHABLE, SORT_ORDER, STORAGE_VERSION, URL, XIAOYA, INDEX_FILE, PASSWORD, TOKEN, FOLDER)
VALUES (4, FALSE, '七米蓝', TRUE, 4, 4, 'https://al.chirmyram.com', FALSE, NULL, '', '', '');
```

### 用户测试方案

1. 首页/搜索搜一部电影（如《阿凡达》《流浪地球》）：结果中会出现网盘视频文件
   （来源显示 `alist-tvbox`，标题为文件名）；
2. 打开该视频：详情页可进入播放，播放地址为 `http://127.0.0.1:4567/p/...`（本地代理）；
3. 夸克盘里放一部电影后，首页搜索同样能搜到（走 `/quark` 转码直链，缓冲更稳）；
4. 源状态页（`/api/sources`）能看到 `alist-tvbox` 条目；
5. 重启整套服务（restart.bat 后 main 自拉起 AList + jar），`/api/ops/status` 正常。

## 参考链接

- 仓库：https://github.com/power721/alist-tvbox
- 中文文档：https://github.com/power721/alist-tvbox/blob/master/doc/README_zh.md
- API 文档：https://github.com/power721/alist-tvbox/blob/master/api.md
- 发布页：https://github.com/power721/alist-tvbox/releases
- AList 发布页：https://github.com/AlistGo/alist/releases
- Windows 支持 issue：https://github.com/power721/alist-tvbox/issues/291
