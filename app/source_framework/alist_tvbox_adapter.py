"""alist-tvbox 源适配器：走 TVBox 协议 /vod（搜索/详情/播放）。

播放地址为 alist-tvbox 的本地代理 /p/{token}/{id}（带 AList/网盘 Cookie 与多线程加速），
供浏览器/播放器直接拉流。搜索覆盖本地 AList（夸克等网盘）+ 公共 AList 站点。
"""
import logging
import re
import urllib.parse
import urllib.request

import config as cfg

logger = logging.getLogger("alist_tvbox")

NAME = "alist-tvbox"
_VIDEO_EXTS = (".mp4", ".mkv", ".flv", ".ts", ".m3u8", ".avi", ".rmvb", ".wmv", ".mov", ".webm", ".mpg", ".mpeg")

_EXT_RE = re.compile(r"\.(mp4|mkv|flv|ts|m3u8|avi|rmvb|wmv|mov|webm|mpg|mpeg)$", re.I)
_TAG_RE = re.compile(
    "|".join(re.escape(t) for t in (
        "1080p", "720p", "480p", "2160p", "2k", "4k", "8k", "uhd", "hdr",
        "x264", "x265", "h264", "h265", "hevc", "avc", "av1",
        "bluray", "brrip", "bdrip", "web-dl", "webdl", "webrip",
        "hdtv", "hdrip", "dvdrip", "remux", "dolby", "atmos",
        "aac", "ac3", "dts", "flac", "truehd",
        "国语", "粤语", "中字", "简中", "繁中", "双语", "内嵌字幕", "外挂字幕",
        "完整版", "未删减", "加长版", "导演剪辑版", "特别版",
    )),
    re.I,
)


def clean_file_title(filename: str) -> str:
    """从网盘文件名构建干净的视频标题。
    示例：[阿凡达].Avatar.2009.mkv -> 阿凡达
          [流浪地球1080Px264].mkv -> 流浪地球
          流浪地球2.Wandering.Earth.2.2023.mkv -> 流浪地球2
    """
    s = str(filename or "").strip()
    s = _EXT_RE.sub("", s)
    s = _TAG_RE.sub(" ", s)
    s = re.sub(r"[._\-\[\]\u3010\u3011()\uff08\uff09{}\uff5b\uff5d\s]+", " ", s)
    tokens = [t for t in s.split() if t and not re.fullmatch(r"\d{4}", t)]
    # 优先取第一个含汉字（可带数字）的连续片段，例如“流浪地球2”
    for t in tokens:
        m = re.match(r"[\u4e00-\u9fff][\u4e00-\u9fff0-9]*", t)
        if m and len(m.group(0)) >= 2:
            return m.group(0)
    # 无中文时取前 3 个英文词作为标题
    words = [t for t in tokens if re.search(r"[A-Za-z]", t)]
    if words:
        return " ".join(words[:3])
    return tokens[0] if tokens else str(filename or "")


class AlistTvboxSource:
    """与 MaccmsSource/DrpySource 对齐的最小适配器（搜索/详情/播放解析）。"""

    name = NAME
    source_type = "alist_tvbox"
    enabled = True
    adult = False
    base_url = cfg.ALIST_TVBOX_BASE_URL
    api_url = cfg.ALIST_TVBOX_BASE_URL
    header_profile = {}

    def _get(self, url: str, timeout: float) -> dict | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                import json
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:
            logger.warning(f"[{NAME}] 请求失败: {e}")
            return None

    def _is_video(self, name: str) -> bool:
        n = (name or "").lower()
        return any(n.endswith(ext) for ext in _VIDEO_EXTS)

    def _normalize(self, item: dict) -> dict:
        title = item.get("vod_name") or item.get("name") or ""
        vid = item.get("vod_id") or item.get("id") or ""
        clean_title = clean_file_title(title)
        return {
            "title": clean_title,
            "type": "movie",
            "cover": item.get("vod_pic") or "",
            "description": item.get("vod_content") or title or "",
            "year": None,
            "area": "",
            "director": "",
            "actors": "",
            "rating": None,
            "source": NAME,
            "source_url": str(vid),
            "genre": "",
            "hits": 0,
            "hits_week": 0,
            "douban_score": None,
            "remarks": title or (item.get("vod_remarks") or ""),
        }

    def search(self, keyword: str, timeout: float = None) -> list[dict]:
        """搜索：只保留视频文件类结果（网盘里大量 epub/azw3 书籍不进入片库）。"""
        timeout = timeout or 10
        url = f"{self.base_url}/vod?ac=detail&wd={urllib.parse.quote(str(keyword))}"
        data = self._get(url, timeout)
        items = (data or {}).get("list") or []
        out = []
        for it in items:
            title = it.get("vod_name") or ""
            if not self._is_video(title):
                continue
            out.append(self._normalize(it))
        return out

    def get_detail(self, url_or_id: str) -> tuple[dict | None, list[dict]]:
        """详情：返回视频信息与剧集列表（网盘文件通常单集）。"""
        url = f"{self.base_url}/vod?ac=detail&ids={urllib.parse.quote(str(url_or_id), safe='$')}"
        data = self._get(url, 15)
        items = (data or {}).get("list") or []
        if not items:
            return None, []
        detail = self._normalize(items[0])
        play_url = items[0].get("vod_play_url") or ""
        episodes = []
        if play_url:
            for i, seg in enumerate(play_url.split("$$$"), 1):
                if seg:
                    episodes.append({"episode_num": i, "episode_title": "", "play_url": seg})
        return detail, episodes

    def resolve_play_lines(self, url_or_id: str, episode: int = None,
                           max_candidates: int = 6) -> list[dict]:
        """播放解析：走 /vod ac=play 拿到本地代理地址；失败时回退 detail 的 play_url。"""
        out = []
        idx = episode or 1
        url = (f"{self.base_url}/vod?ac=play&ids={urllib.parse.quote(str(url_or_id), safe='$')}"
               f"&playIndex={idx}")
        data = self._get(url, 15)
        for it in ((data or {}).get("list") or []):
            pu = it.get("play_url") or it.get("url") or ""
            if pu:
                out.append({"url": pu, "header": {}})
        if not out:
            _, episodes = self.get_detail(url_or_id)
            for ep in episodes:
                if ep.get("play_url"):
                    out.append({"url": ep["play_url"], "header": {}})
        return out[:max_candidates]

    def get_play_url(self, url_or_id: str) -> str | None:
        lines = self.resolve_play_lines(url_or_id, None, 1)
        return lines[0]["url"] if lines else None


_INSTANCE = None


def get_source() -> AlistTvboxSource | None:
    global _INSTANCE
    if not cfg.ALIST_TVBOX_ENABLED:
        return None
    if _INSTANCE is None:
        _INSTANCE = AlistTvboxSource()
    return _INSTANCE
