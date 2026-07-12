# 🔍 如何配置视频源

## 一句话

找**苹果CMS（MacCMS）资源站**的 API 地址，填进配置文件就能用。

---

## 什么是 MacCMS 资源站？

国内大量视频站使用苹果CMS（MacCMS）建站。这类站点绝大多数都开放了**标准化数据接口**，地址通常像这样：

```
http://某个视频站.com/api.php/provide/vod?ac=list
```

只要一个网站支持这个接口，我们的软件就能获取它的所有视频数据。

---

## 三步配置

### 第一步：找一个 MacCMS 资源站

在浏览器打开一个视频站，然后在后面加上 `/api.php/provide/vod?ac=list&at=json&pagesize=5`，如果返回 JSON 数据（有 `code`、`list`、`total` 等字段），说明这个站可用。

### 第二步：填入配置文件

打开 `data/maccms_sources.json`，把源地址填进去：

```json
{
  "sources": [
    {
      "name": "我的源",
      "base_url": "http://你找到的网站.com",
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

### 第三步：触发爬取

启动软件后，访问：
- `http://localhost:8080/api/crawl/trigger` — 手动触发爬取
- 或者等自动定时爬取（每6小时一次）

---

## MacCMS 源特征速查

一个典型的 MacCMS 资源站接口：

| 项目 | 说明 |
|---|---|
| API 地址 | `{base_url}/api.php/provide/vod` |
| 分类参数 | `t=1`(电影) `t=2`(剧集) `t=3`(综艺) `t=4`(动漫) |
| 搜索参数 | `wd=关键词` |
| 返回格式 | JSON，字段名: `vod_id`, `vod_name`, `vod_pic`, `vod_play_url` |
| 播地址格式 | `第1集$url#第2集$url#...` |

---

## 多源策略

**建议配置 2-3 个源**。如果一个失效，自动用其他的。

在 `maccms_sources.json` 中添加多条即可：

```json
{
  "sources": [
    { "name": "源A", "base_url": "https://site-a.com", "enabled": true },
    { "name": "源B", "base_url": "https://site-b.com", "enabled": true }
  ]
}
```

---

## 常见问题

**Q: 为什么有些站加了 /api.php/provide/vod 返回404？**
A: 那个站可能不是 MacCMS 建的，或者没有开放 API。换一个。

**Q: 返回了数据但图片显示不了？**
A: 图片防盗链，正常。视频能播就行。

**Q: 源用一段时间失效了？**
A: 视频站的域名会变，这是常态。换一个源更新到配置文件即可，软件架构不变。
