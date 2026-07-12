"""
苹果CMS (MacCMS/Apple CMS) V10 API 视频源适配器
══════════════════════════════════════════════════════════

MacCMS 是国内绝大多数视频站使用的建站系统，其 V10 版本定义了标准化
的聚合 API 协议（/api.php/provide/vod/），数千个视频站使用该协议对外
提供数据。这意味着写一个适配器 = 接入数千个源。

API 协议规范：
  URL:   {base_url}/api.php/provide/vod
  参数:  ac=list|detail|videolist  动作类型
          at=json|xml              返回格式（默认 JSON）
          t=分类ID                 按分类过滤
          pg=页码                  分页
          wd=关键词                搜索
          h=小时                   最近N小时更新
          ids=ID列表              按ID获取
          pagesize=每页条数        最大100

JSON 响应字段（MacCMS 标准）：
  code      : 状态码（1=成功）
  page      : 当前页
  pagecount : 总页数
  total     : 总数
  list      : 视频列表 [{vod_id, vod_name, vod_pic, vod_actor, vod_director,
              vod_content, vod_year, vod_area, vod_play_from, vod_play_url, ...}]

  vod_play_url 格式: "第1集$播放地址1#第2集$播放地址2#..."
  vod_play_from 格式: "播放器来源名称" (如 "qiyi", "m3u8", "youku")

如何使用：
  1. 找一个公开的 MacCMS 资源站 API 地址
  2. 在 config.json 或 maccms_sources.json 中添加该地址
  3. 源会自动产出搜索、分类、详情、播放数据

稳定性建议：
  - 配置多几个源做故障转移
  - 源的域名可能变化，但 API 协议不变
  - 社区共享的 TVBox 源配置可直接复用
"""
import json
import random
import re
import logging
from typing import Optional
from urllib.parse import urljoin, urlencode, quote

import cloudscraper
from config import USER_AGENTS, REQUEST_TIMEOUT

logger = logging.getLogger("maccms")

# ─── 分类 ID 映射 ───
# MacCMS 默认分类ID，不同站可能不同，可自定义
DEFAULT_CATEGORY_IDS = {
    "movie": "1",
    "tv": "2",
    "variety": "3",
    "anime": "4",
}

# ─── 类型判断关键词 ───
TYPE_KEYWORDS = {
    "movie": ["电影", "喜剧", "动作", "科幻", "爱情", "恐怖", "悬疑", "剧情片", "喜剧片", "动作片", "动漫电影"],
    "tv": ["电视剧", "连续剧", "国产剧", "韩剧", "美剧", "日剧"],
    "variety": ["综艺", "真人秀", "脱口秀", "选秀"],
    "anime": ["动漫", "动画", "番剧"],
}


class MaccmsSource:
    """
    MacCMS V10 API 视频源

    通过标准的 MacCMS 聚合 API 获取视频数据，无需页面爬取。
    支持搜索、分类浏览、详情查看、播放地址获取。
    """

    def __init__(self, name: str, base_url: str, category_map: dict = None,
                 enabled: bool = True, proxy: str = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api.php/provide/vod"
        self.category_map = category_map or DEFAULT_CATEGORY_IDS.copy()
        self.enabled = enabled
        self.proxy = proxy
        self._scraper = cloudscraper.create_scraper()

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": self.base_url,
            "Accept": "application/json, text/plain, */*",
        }

    def _request(self, params: dict) -> Optional[dict]:
        """调用 MacCMS API（直接用 urllib，避免 cloudscraper 卡住）"""
        import urllib.request, urllib.parse
        params.setdefault("ac", "list")
        params.setdefault("at", "json")
        qs = urllib.parse.urlencode(params)
        url = f"{self.api_url}?{qs}"
        try:
            req = urllib.request.Request(url, headers=self._headers())
            resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("code") != 1:
                logger.warning(f"[{self.name}] API error: code={data.get('code')}")
                return None
            return data
        except json.JSONDecodeError:
            logger.warning(f"[{self.name}] 返回非 JSON，可能不是 MacCMS 站点")
            return None
        except Exception as e:
            logger.warning(f"[{self.name}] 请求失败: {e}")
            return None

    # ─── 公开接口 ───

    def search(self, keyword: str) -> list[dict]:
        """搜索视频"""
        params = {"ac": "list", "wd": keyword}
        data = self._request(params)
        if not data:
            return []
        return self._normalize_list(data.get("list") or [])

    def get_list(self, category: str = "movie", pagesize: int = 100) -> list[dict]:
        """
        获取分类列表

        注意：MacCMS 的 t=category_id 过滤在某些源上可能无效（视频的分类标签
        与实际 type_id 不一致）。如果单分类返回数据太少，我们会回退到获取
        全部最新视频然后用 _infer_type 自动分类。
        """
        cat_id = self.category_map.get(category)
        items = []
        total = 0

        # 先尝试按分类获取
        if cat_id:
            params = {"ac": "videolist", "t": cat_id, "pg": "1", "pagesize": str(pagesize)}
            data = self._request(params)
            if data:
                items = data.get("list") or []
                total = int(data.get("total", 0))

        # 如果分类获取数量太少，回退到全量获取
        if len(items) < 5:
            params = {"ac": "videolist", "pg": "1", "pagesize": str(max(pagesize, 100))}
            data = self._request(params)
            if data:
                all_items = data.get("list") or []
                # 用 _infer_type 过滤
                items = [i for i in all_items if self._infer_type(i) == category]
                # 如果还不够，多取几页
                if len(items) < 20:
                    for pg in range(2, 5):
                        params["pg"] = str(pg)
                        data = self._request(params)
                        if not data:
                            break
                        page_items = data.get("list") or []
                        items += [i for i in page_items if self._infer_type(i) == category]
                        if len(items) >= 20:
                            break

        return self._normalize_list(items[:pagesize])

    def get_detail(self, vod_id_or_url: str) -> tuple[dict, list[dict]]:
        """获取视频详情和剧集列表"""
        # 支持传入 URL 或 ID
        vod_id = self._extract_id(vod_id_or_url)
        if not vod_id:
            return {}, []

        params = {"ac": "detail", "ids": vod_id}
        data = self._request(params)
        if not data:
            return {}, []

        items = data.get("list") or []
        if not items:
            return {}, []

        return self._normalize_detail(items[0])

    def get_play_url(self, url_or_id: str) -> Optional[str]:
        """
        获取可播放的视频地址。

        MacCMS API 的 vod_play_url 已经包含播放地址，
        格式为 "第1集$url1#第2集$url2#..."，直接提取即可。
        """
        # 如果是 URL，尝试获取详情
        vod_id = self._extract_id(url_or_id)
        if vod_id:
            _, episodes = self.get_detail(vod_id)
            if episodes:
                return episodes[0].get("play_url")

        # 如果是直接的视频流地址，直接返回
        if any(url_or_id.endswith(ext) for ext in [".mp4", ".m3u8", ".flv", ".ts"]):
            return url_or_id

        # 如果是网页 URL 且有 vod_id 模式
        m = re.search(r'/v/(\d+)', url_or_id)
        if m:
            _, episodes = self.get_detail(m.group(1))
            if episodes:
                return episodes[0].get("play_url")

        return url_or_id

    # ─── 内部处理 ───

    def _extract_id(self, url_or_id: str) -> Optional[str]:
        """从 URL 或 ID 中提取视频 ID"""
        url_or_id = str(url_or_id)
        # 纯数字就是 ID
        if url_or_id.isdigit():
            return url_or_id
        # 从 URL 中提取
        m = re.search(r'/(vod|video|v|detail)/(\d+)', url_or_id)
        if m:
            return m.group(2)
        m = re.search(r'[?&]id=(\d+)', url_or_id)
        if m:
            return m.group(2)
        return None

    def _normalize_list(self, items: list) -> list[dict]:
        """标准化列表数据"""
        results = []
        for item in items:
            if not item or not item.get("vod_name"):
                continue
            video_type = self._infer_type(item)
            results.append({
                "title": item["vod_name"],
                "type": video_type,
                "cover": item.get("vod_pic", ""),
                "description": (item.get("vod_content") or "")[:200],
                "year": self._safe_int(item.get("vod_year")),
                "area": item.get("vod_area", ""),
                "director": (item.get("vod_director") or "")[:100],
                "actors": (item.get("vod_actor") or "")[:200],
                "rating": self._safe_float(item.get("vod_score") or item.get("vod_rating")),
                "source": self.name,
                "source_url": self._make_detail_url(item.get("vod_id")),
            })
        return results

    def _normalize_detail(self, item: dict) -> tuple[dict, list[dict]]:
        """标准化详情数据"""
        video_info = {
            "title": item.get("vod_name", ""),
            "type": self._infer_type(item),
            "cover": item.get("vod_pic", ""),
            "description": (item.get("vod_content") or "")[:500],
            "year": self._safe_int(item.get("vod_year")),
            "area": item.get("vod_area", ""),
            "director": (item.get("vod_director") or "")[:100],
            "actors": (item.get("vod_actor") or "")[:200],
            "rating": self._safe_float(item.get("vod_score") or item.get("vod_rating")),
            "source": self.name,
            "source_url": self._make_detail_url(item.get("vod_id")),
        }

        # 解析剧集
        episodes = self._parse_episodes(item)
        return video_info, episodes

    def _parse_episodes(self, item: dict) -> list[dict]:
        """
        解析 MacCMS 的剧集数据。

        vod_play_url 格式: "第1集$url1#第2集$url2#..."
        或者多个播放源: "第1集$url1#第2集$url2$$$第1集$url_a#..."

        多个源用 $$$ 分隔，通常第一个是 HTML 播放页，后面的有直链。
        会优先选择含有 .m3u8/.mp4 直链的源。
        """
        episodes = []
        play_url = item.get("vod_play_url", "")

        if not play_url:
            return []

        # 处理多播放源：用 $$$ 分隔
        sources = re.split(r'\${3,}', play_url)
        if not sources:
            return []

        # 判断一个 URL 是不是直链视频
        def is_direct_video(url: str) -> bool:
            return any(url.endswith(ext) for ext in [".m3u8", ".mp4", ".flv", ".ts", ".mkv"])

        # 解析单个源的剧集
        def parse_source(source_str: str) -> list[dict]:
            parts = re.split(r'#+', source_str.strip())
            result = []
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # 格式: "标题$地址"
                m = re.match(r'(.+?)\$(https?://[^\$]+)', part)
                if m:
                    title = m.group(1).strip()
                    url = m.group(2).strip()
                else:
                    # 尝试反转
                    m2 = re.match(r'(https?://[^\$]+)\$(.+)', part)
                    if m2:
                        url = m2.group(1).strip()
                        title = m2.group(2).strip()
                    else:
                        title = f"第{len(result) + 1}集"
                        url = part

                if url and not url.startswith("http"):
                    url = self.base_url + url

                result.append({
                    "episode_num": len(result) + 1,
                    "episode_title": title,
                    "play_url": url,
                    "is_available": 1,
                })
            return result

        # 解析所有源
        parsed_sources = [parse_source(s) for s in sources if s.strip()]

        if not parsed_sources:
            return []

        # 优先选择有直链视频的源
        for src in parsed_sources:
            if src and is_direct_video(src[0]["play_url"]):
                return src

        # 回退到第一个源
        return parsed_sources[0]

    def _make_detail_url(self, vod_id) -> str:
        """生成详情页 URL"""
        if not vod_id:
            return ""
        return f"{self.base_url}/vod/{vod_id}/"

    def _infer_type(self, item: dict) -> str:
        """根据数据推断视频类型"""
        # 先检查 type_id / type_name
        type_name = (item.get("type_name") or "").lower()
        type_id = str(item.get("type_id") or "")

        # 反向映射 category_map
        rev_map = {v: k for k, v in self.category_map.items()}
        if type_id in rev_map:
            return rev_map[type_id]

        # 检查分类名称
        for vtype, keywords in TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in type_name or kw in type_id:
                    return vtype

        # 检查标题
        title = item.get("vod_name", "")
        for vtype, keywords in TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in title:
                    return vtype

        # 检查分类标签
        class_name = (item.get("vod_class") or "")
        for vtype, keywords in TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in class_name:
                    return vtype

        return "movie"  # 默认

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if not val:
            return None
        try:
            # 处理 "2026(中国大陆)" 这类格式
            m = re.search(r'(\d{4})', str(val))
            return int(m.group(1)) if m else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if not val:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None


# ─── 源管理器 ───

class MaccmsSourceManager:
    """
    MacCMS 源管理器

    管理多个 MacCMS 源，支持故障转移、结果合并。
    源配置存储在 sources/maccms.json 中，方便用户添加/删除源。
    """

    def __init__(self, config_path: str = None):
        self._sources: list[MaccmsSource] = []
        self._loaded = False
        self._config_path = config_path

    def load_default(self):
        """加载默认源（源代码中内置的几个示例——需要用户自行修改 base_url）"""
        self._sources = []
        self._loaded = True

    def load_from_config(self, config_path: str):
        """从 JSON 配置文件加载源列表"""
        import json
        import os
        self._sources = []
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                for item in config.get("sources", []):
                    if not item.get("enabled", True):
                        continue
                    source = MaccmsSource(
                        name=item["name"],
                        base_url=item["base_url"],
                        category_map=item.get("category_map"),
                        enabled=True,
                        proxy=item.get("proxy"),
                    )
                    self._sources.append(source)
                logger.info(f"从配置加载了 {len(self._sources)} 个 MacCMS 源")
        except Exception as e:
            logger.error(f"加载 MacCMS 配置失败: {e}")

    def add_source(self, source: MaccmsSource):
        """添加源"""
        self._sources.append(source)
        self._loaded = True

    def get_all(self) -> list[MaccmsSource]:
        """获取所有启用的源"""
        return self._sources

    def get_by_name(self, name: str) -> Optional[MaccmsSource]:
        for s in self._sources:
            if s.name == name:
                return s
        return None


# ─── 全局实例 ───

_manager = MaccmsSourceManager()


def get_manager() -> MaccmsSourceManager:
    return _manager


def load_sources(config_path: str = None):
    """从配置文件加载源"""
    _manager.load_from_config(config_path)
    # 如果没有成功加载任何源，加载默认示例
    if not _manager.get_all():
        _manager.load_default()
        logger.info("未找到 MacCMS 配置，使用默认设置")


# ─── 与现有爬虫框架的适配 ───

# 将 MaccmsSource 包装成兼容现有爬虫框架的接口

def get_maccms_crawlable_sources() -> list:
    """返回兼容 app.sources.VideoSource 接口的包装对象（自动加载配置）"""
    import os
    if not _manager.get_all():
        config_path = os.path.join(os.path.dirname(__file__), "..", "data", "maccms_sources.json")
        if os.path.exists(config_path):
            _manager.load_from_config(config_path)
    sources = _manager.get_all()
    if not sources:
        return []
    wrapped = []
    for src in sources:
        wrapped.append(_MaccmsWrapper(src))
    return wrapped


class _MaccmsWrapper:
    """MaccmsSource 到 VideoSource 接口的适配器"""

    def __init__(self, source: MaccmsSource):
        self._src = source
        self.name = source.name
        self.base_url = source.base_url
        self.enabled = source.enabled

    def search(self, keyword: str) -> list[dict]:
        return self._src.search(keyword)

    def get_list(self, category: str = "movie", pagesize: int = 100) -> list[dict]:
        return self._src.get_list(category, pagesize)

    def get_detail(self, url: str) -> tuple[dict, list[dict]]:
        return self._src.get_detail(url)

    def get_play_url(self, url: str) -> Optional[str]:
        return self._src.get_play_url(url)
