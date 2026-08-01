# drpyS（JS 爬虫生态）接入指南

> 阶段 B：通过 drpyS 侧车接入 TVBox JS 爬虫生态（200+ 源），
> 与现有 MacCMS 源统一走搜索/播放链/健康检查。

## 安装（miniPC，全部装在 D 盘项目内，不占 C 盘）

```bat
scripts\setup_drpys.ps1
scripts\start_drpys.bat
```

脚本会：
1. 从 npmmirror 下载便携 Node（v22）到 `sidecar\node`（D 盘）；
2. 从 ghfast.top（备用 ghproxy.cn）下载 drpys 源码到 `sidecar\drpys`，用 Python tarfile 解压（避免文件名乱码）；
3. 用 npmmirror 安装 npm 依赖（缓存放 `sidecar\npm-cache`）。

之后主程序 `main.py` 会自动拉起 drpyS（127.0.0.1:5757），无需手动启动。

## 升级/更新 drpyS

```bat
scripts\update_drpys.ps1
scripts\restart.bat
```

更新会保留旧版本备份（`sidecar\drpys.bak.时间戳`），失败可回退。

## 源管理

- 状态页：`GET /api/drpy/status`（总数/启用数/成人数/每源 enabled/adult/dead）
- 手动刷新：`POST /api/drpy/refresh`
- 覆盖配置：编辑 `data/source_registry.json`（gitignore，不入库）：

```json
{
  "_version": 1,
  "drpy_sources": {
    "荐片[优](DS)": {"enabled": false},
    "某个盘源[盘]": {"enabled": true, "adult": false}
  }
}
```

## 默认启用策略

- 自动启用影视点播类源（影视/影院/剧/动漫/动画/电影/综艺/直连/播放 等关键词）；
- 自动禁用：音乐/听书/FM/漫画/壁纸/游戏/直播/球类、[搜]盘搜、[磁]磁力、[模]模板、
  [盘]网盘类（需要登录态，阶段 C 再接入）、设置/资源管理/测试类；
- 自动标记成人：`[密]` 标签与 18av/草榴/麻豆 等关键词，标记源不参与普通搜索/爬取，
  名称写入 `data/drpy_adult_sources.json` 供全局隔离使用；
- 健康检查连续 3 次失败的源自动隔离，恢复后自动启用。

## 故障排查

- drpyS 起不来：看 `sidecar\logs\drpys.err.log`；确认 Node 版本（需 18-23，v22 已验证）。
- 源请求失败：`sidecar\logs\drpys.log` 有每个模块的错误日志。
- 文件名乱码：只用 `setup_drpys.ps1`（Python 解压）；不要用 Windows 自带 tar 解压。

## 采集源自动并入（阶段 C）

drpyS 自带 `json/采集20xx静态.json` 采集站清单（爱奇艺/腾讯/暴风/非凡/无尽/金鹰等 90+ 端点）。
每日同步（或 `POST /api/ops/sync-now`）会自动：

- 跳过 `[密]` 成人清单与名称含 AV/成人/番号 的源；
- 按 base_url/名称去重（重名自动加"（采集）"或序号）；
- 并行测通（`ac=list` 有数据）才并入 `data/maccms_community.json`；
- 健康检查继续自动隔离失效源。

## 便携打包（miniPC 部署）

```bat
python scripts\build_portable.py
```

输出 `portable\TvMediaCenter\`（含嵌入式 Python + fastapi/uvicorn/requests、
项目代码、Node + drpys 侧车）和 zip 包。目标机器解压后运行 `start-all.bat` 即可，
全部文件可放在 D 盘，不写 C 盘。

## 关于 TVBox JAR 爬虫（饭太硬 csp_*）的结论

饭太硬 49 个 csp_* 源是 **Android DEX + ARM 原生 .so 加固**（ftyguard），
只能在安卓设备/电视盒上运行，Windows miniPC 的 JVM 无法加载。因此阶段 C 不采用
JAR 宿主方案，改用两条更可行的路线：drpyS JS 爬虫生态（阶段 B，已含荐片/玩偶/果果等）
+ MacCMS 采集站大池（本页上一节）。网盘类源（夸克/UC/阿里）需要登录凭据，默认关闭，
后续如需接入再单独做。
