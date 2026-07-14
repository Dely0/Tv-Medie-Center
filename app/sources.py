r"""
视频源定义 — 每个源配置爬取规则和解析逻辑

如何添加视频源：
──────────────────
1. 在下方找到 _SOURCES 列表，将 enabled 设为 True
2. 把 base_url 替换为实际视频站的地址
3. 如果网站结构不同，继承 SourceGeneric 并重写 parse_list_html / parse_detail_html

常用源配置示例：
  - 搜索URL: {base_url}/search/{keyword}.html
  - 分类URL: {base_url}/movie/  {base_url}/tv/
              {base_url}/zongyi/  {base_url}/dongman/
  - 剧集链接选择器: .stui-content__playlist a, .module-play-list a

使用通用型源（SourceGeneric）通常只需改 base_url + 选择器即可适配大部分CMS建站。
"""
import re
import random
from typing import Optional
from config import USER_AGENTS, REQUEST_TIMEOUT


class VideoSource:
    """视频源基类"""

    name: str = ""
    base_url: str = ""
    enabled: bool = True

    def __init__(self):
        self.scraper = None
        self.session = None

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": self.base_url,
        }

    def _init_scraper(self):
        if self.scraper is None:
            import cloudscraper
            self.scraper = cloudscraper.create_scraper()

    def _get(self, url: str, params: dict = None) -> Optional[str]:
        """带重试的 GET 请求"""
        self._init_scraper()
        for attempt in range(3):
            try:
                resp = self.scraper.get(
                    url, params=params, headers=self._headers(),
                    timeout=REQUEST_TIMEOUT, allow_redirects=True
                )
                if resp.status_code == 200:
                    return resp.text
            except Exception:
                if attempt == 2:
                    return None

    def _post(self, url: str, data: dict = None) -> Optional[str]:
        self._init_scraper()
        for attempt in range(3):
            try:
                resp = self.scraper.post(
                    url, data=data, headers=self._headers(),
                    timeout=REQUEST_TIMEOUT, allow_redirects=True
                )
                if resp.status_code == 200:
                    return resp.text
            except Exception:
                if attempt == 2:
                    return None

    def _parse_html(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def search(self, keyword: str) -> list[dict]:
        """搜索，返回 [{title, type, cover, source_url, ...}]"""
        raise NotImplementedError

    def get_list(self, category: str = "") -> list[dict]:
        """获取分类列表"""
        raise NotImplementedError

    def get_detail(self, url: str) -> tuple[dict, list[dict]]:
        """获取详情和剧集，返回 (video_info, [episode])"""
        raise NotImplementedError

    def get_play_url(self, url: str) -> Optional[str]:
        """解析真实播放地址"""
        raise NotImplementedError


# ──────────────────────────────────────────
# 源 1: 通用资源站模式
# ──────────────────────────────────────────
class SourceGeneric(VideoSource):
    """通用型视频源 — 针对常见 CMS 建站模式"""

    name = "通用源"
    base_url = "https://example.com"
    enabled = False  # 需要用户配置实际地址

    # URL 模板 — 子类覆盖
    search_url = "{base_url}/search/{keyword}.html"
    category_urls = {
        "movie": "{base_url}/movie/",
        "tv": "{base_url}/tv/",
        "variety": "{base_url}/zongyi/",
        "anime": "{base_url}/dongman/",
    }

    def parse_list_html(self, html: str) -> list[dict]:
        """从列表页提取视频信息 — 子类按需覆盖"""
        soup = self._parse_html(html)
        results = []
        for item in soup.select(".stui-vodlist__item, .module-item, .vodlist-item, li"):
            link = item.select_one("a[href]")
            if not link:
                continue
            href = link.get("href", "")
            title = link.get("title", "") or link.text.strip()
            img = item.select_one("img")
            cover = img.get("data-original", "") or img.get("src", "") if img else ""
            # 提取类型提示
            tip = item.select_one(".pic-text, .tag, .score")
            results.append({
                "title": title.strip(),
                "cover": cover,
                "source_url": href if href.startswith("http") else self.base_url + href,
                "type_hint": tip.text.strip() if tip else "",
            })
        return results

    def parse_detail_html(self, html: str, source_url: str) -> tuple[dict, list[dict]]:
        """从详情页提取视频信息和剧集"""
        soup = self._parse_html(html)
        # 标题
        title_el = soup.select_one("h1, .stui-content__title, .page-title, .module-title")
        title = title_el.text.strip() if title_el else ""

        # 封面
        cover_el = soup.select_one(
            ".stui-content__thumb img, .module-item-pic img, .detail-pic img"
        )
        cover = ""
        if cover_el:
            cover = cover_el.get("data-original", "") or cover_el.get("src", "")

        # 描述
        desc_el = soup.select_one(
            ".stui-content__desc, .module-info-content, .detail-description, .desc"
        )
        desc = desc_el.text.strip() if desc_el else ""

        # 元数据
        year = None
        area = ""
        director = ""
        actors = ""
        rating = None
        for p in soup.select(".stui-content__detail p, .module-info p, .detail-info p"):
            text = p.text.strip()
            if "年份" in text or "上映" in text:
                m = re.search(r"(\d{4})", text)
                if m:
                    year = int(m.group(1))
            if "地区" in text or "国家" in text:
                area = text.split("：")[-1].strip() if "：" in text else text
            if "导演" in text or "主演" in text:
                val = text.split("：")[-1].strip() if "：" in text else ""
                if "导演" in text:
                    director = val
                else:
                    actors = val

        # 评分
        score_el = soup.select_one(".score, .rating, .star")
        if score_el:
            m = re.search(r"([\d.]+)", score_el.text.strip())
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        # 判断类型
        video_type = "movie"
        type_text = soup.select_one(".stui-content__tag a, .module-tag, .type-tag")
        if type_text:
            t = type_text.text.strip()
            if any(k in t for k in ["剧", "连续剧", "电视剧"]):
                video_type = "tv"
            elif any(k in t for k in ["综艺"]):
                video_type = "variety"
            elif any(k in t for k in ["动漫", "动画"]):
                video_type = "anime"
            # type_hint 里也可能有
            if any(k in t for k in ["电影", "片"]):
                video_type = "movie"

        video_info = {
            "title": title,
            "type": video_type,
            "cover": cover,
            "description": desc[:500],
            "year": year,
            "area": area,
            "director": director[:100],
            "actors": actors[:200],
            "rating": rating,
            "source": self.name,
            "source_url": source_url,
        }

        # 剧集列表
        episodes = []
        seen = set()
        for link in soup.select(
            ".stui-content__playlist a, .module-play-list a, .playlist a, "
            ".episode-list a, .play-list a, [class*='playlist'] a"
        ):
            href = link.get("href", "")
            if not href or href == "#":
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif not href.startswith("http"):
                href = self.base_url + href
            if href in seen:
                continue
            seen.add(href)
            ep_text = link.text.strip()
            ep_num = len(episodes) + 1
            m = re.search(r"(\d+)", ep_text)
            if m:
                ep_num = int(m.group(1))
            episodes.append({
                "episode_num": ep_num,
                "episode_title": ep_text,
                "play_url": href,
                "is_available": 1,
            })

        return video_info, episodes

    def search(self, keyword: str) -> list[dict]:
        url = self.search_url.format(base_url=self.base_url, keyword=keyword)
        html = self._get(url)
        if not html:
            return []
        return self.parse_list_html(html)

    def get_list(self, category: str = "") -> list[dict]:
        url_t = self.category_urls.get(category)
        if not url_t:
            return []
        url = url_t.format(base_url=self.base_url)
        html = self._get(url)
        if not html:
            return []
        return self.parse_list_html(html)

    def get_detail(self, url: str) -> tuple[dict, list[dict]]:
        html = self._get(url)
        if not html:
            return {}, []
        return self.parse_detail_html(html, url)

    def get_play_url(self, url: str) -> Optional[str]:
        """解析视频播放页面，提取真实视频地址"""
        html = self._get(url)
        if not html:
            return None
        soup = self._parse_html(html)
        # 常见视频元素
        for src in soup.select("video source, video"):
            s = src.get("src", "")
            if s:
                return s if s.startswith("http") else self.base_url + s
        # 各种 iframe
        for iframe in soup.select("iframe[src]"):
            s = iframe["src"]
            if "player" in s.lower() or "play" in s.lower():
                return self._extract_from_player(s)
        # JS 变量中的视频地址
        scripts = soup.select("script")
        for sc in scripts:
            text = sc.string or ""
            for p in ["video_url", "play_url", "url", "mp4", "m3u8"]:
                m = re.search(rf'["\']{p}["\'][^,]*[,:]\s*["\']([^"\']+)["\']', text, re.I)
                if m:
                    return m.group(1)
            m = re.search(r'(https?://[^"\']+\.(m3u8|mp4)[^"\']*)', text)
            if m:
                return m.group(1)
        # 直接是视频地址的情况
        return url if any(url.endswith(e) for e in [".mp4", ".m3u8", ".flv"]) else None

    def _extract_from_player(self, player_url: str) -> Optional[str]:
        """从播放器页面提取"""
        html = self._get(player_url)
        if not html:
            return None
        m = re.search(r'(https?://[^"\']+\.(m3u8|mp4)[^"\']*)', html)
        return m.group(1) if m else player_url


# ──────────────────────────────────────────
# 源 2: API 接口型
# ──────────────────────────────────────────
class SourceAPI(VideoSource):
    """基于 JSON API 的视频源"""

    name = "API源"
    base_url = "https://example.com"
    enabled = False

    search_api = "{base_url}/api/search?wd={keyword}"
    detail_api = "{base_url}/api/detail?id={id}"

    def search(self, keyword: str) -> list[dict]:
        url = self.search_api.format(base_url=self.base_url, keyword=keyword)
        html = self._get(url)
        if not html:
            return []
        return self._parse_json_list(html)

    def _parse_json_list(self, html: str) -> list[dict]:
        """默认 JSON 解析 — 子类覆盖"""
        import json
        try:
            data = json.loads(html)
            items = data.get("list", data.get("data", data.get("result", [])))
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", item.get("name", "")),
                    "cover": item.get("cover", item.get("pic", "")),
                    "source_url": item.get("url", item.get("link", "")),
                    "type_hint": item.get("type", ""),
                })
            return results
        except json.JSONDecodeError:
            return []

    def get_list(self, category: str = "") -> list[dict]:
        return []

    def get_detail(self, url: str) -> tuple[dict, list[dict]]:
        return {}, []

    def get_play_url(self, url: str) -> Optional[str]:
        return url


# ──────────────────────────────────────────
# 源注册表
# ──────────────────────────────────────────
def get_all_sources() -> list[VideoSource]:
    """获取所有启用的源"""
    return [src for src in _SOURCES if src.enabled]


def get_source_by_name(name: str) -> Optional[VideoSource]:
    for src in _SOURCES:
        if src.name == name and src.enabled:
            return src
    return None


# ═══════════════════════════════════════════
# 视频源配置区 — 在这里添加和配置你的视频源
# ═══════════════════════════════════════════
#
# 使用方法：
# 1. 找一个支持搜索的视频网站（如 xxxzy.com, waptv.com 等）
# 2. 把 base_url 改成该网站的地址
# 3. 如果搜索结果页结构不同，重写 parse_list_html 方法
# 4. 把 enabled 设为 True
#
# SourceGeneric 适配大部分 CMS 建站的视频站：
#   - 搜索 URL:  {base_url}/search/{keyword}.html
#   - 分类 URL:  {base_url}/movie/  /tv/  /zongyi/  /dongman/
#   - 剧集选择器: .stui-content__playlist a  或  .module-play-list a
#
_SOURCES: list[VideoSource] = [
    SourceGeneric(),
    SourceAPI(),

    # ── 示例：添加一个真实源 ──
    # class MySource(SourceGeneric):
    #     name = "我的源"
    #     base_url = "https://example.com"
    #     enabled = True
    #
    #     # 如果列表页结构不同，重写 parse_list_html
    #     def parse_list_html(self, html):
    #         ...
    #
    # MySource(),
]
