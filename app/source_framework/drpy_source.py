"""drpyS（TVBox JS 爬虫）适配器。

drpyS 是 Node 服务端实现 TVBox 爬虫协议的侧车（默认 127.0.0.1:5757），
自带 200+ 源（JS / catvod / drpy2 / hipy）。本模块：
- 拉取并缓存 drpyS 自动生成的 TVBox 配置（/config/1）
- 将每个站点包装为与 MaccmsSource 同构的 DrpySource（search/list/detail/play）
- 修复 Windows 解压导致的 GBK 双重编码乱码源名
- 按 data/source_registry.json 与关键词白名单决定默认启用/成人标记
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import config as cfg
from app.maccms_source import TYPE_KEYWORDS, MaccmsSource

GENRE_MAP = MaccmsSource.GENRE_MAP

logger = logging.getLogger("drpy")

_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE = None  # (ts, data)

# 默认不启用的标签（非影视点播类）
_DISABLED_TAGS = (
    "[听]", "[歌]", "[书]", "[FM]", "[漫画]", "[壁纸]", "[游戏]", "[球]", "[直播]",
    "[磁]", "[模]", "[测试]",
)
# 默认不启用的名称片段
_DISABLED_NAMES = (
    "设置中心", "璁剧疆", "资源管理", "盘搜", "网盘资源", "磁力", "push", "模板",
    "测试", "依赖测试", "配置",
)
# 成人源标签/关键词（drpyS 约定 [密] 为成人，另有 18av / 草榴 / 麻豆 等）
_ADULT_HINTS = ("[密]", "18av", "草榴", "麻豆", "成人", "swag", "onlyfans")
# 影视点播类自动启用关键词
_MOVIE_HINTS = (
    "影视", "影院", "剧", "动漫", "动画", "电影", "综艺", "直连", "播放", "资源",
    "荐片", "立播", "光影", "麦田", "星辰", "月光", "海龟", "奇奇", "果果",
    "琉璃", "樱花", "樱漫", "爱动漫", "酷爱", "包纸", "光社", "动画大全", "动漫大全",
    "3Q", "360影视", "55", "毒舌", "追新", "飞牛", "飞速", "王子", "星抽",
    "剧海", "人人", "多多", "欧哥", "海豚", "极速", "耐看", "云盘", "盘搜",
)


def recover_name(name: str) -> str:
    """修复 Windows 解压 tar 包导致的 GBK 双重编码乱码（如 绔嬫挱 -> 立播）。"""
    if not name:
        return name
    try:
        fixed = name.encode("gbk").decode("utf-8")
        if "\ufffd" not in fixed and fixed.isprintable():
            return fixed
    except Exception:
        pass
    return name


def fetch_drpy_config(force: bool = False, timeout: float = 20.0) -> dict | None:
    """拉取 drpyS 自动生成的 TVBox 配置（带 TTL 缓存）。"""
    global _CONFIG_CACHE
    now = time.time()
    with _CONFIG_LOCK:
        if _CONFIG_CACHE and not force and now - _CONFIG_CACHE[0] < cfg.DRPYS_CONFIG_TTL:
            return _CONFIG_CACHE[1]
    try:
        url = cfg.DRPYS_BASE_URL.rstrip("/") + "/config/1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(data, dict) and isinstance(data.get("sites"), list):
            with _CONFIG_LOCK:
                _CONFIG_CACHE = (now, data)
            return data
    except Exception as e:
        logger.warning(f"drpyS 配置拉取失败: {e}")
    return None


def _read_registry() -> dict:
    """读取 data/source_registry.json 中的显式覆盖项。"""
    try:
        with open(cfg.SOURCE_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("drpy_sources") or {}
    except Exception:
        try:
            # 首次使用：生成默认注册表，供用户按源名覆盖 enabled/adult
            with open(cfg.SOURCE_REGISTRY_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "_version": 1,
                    "_说明": "按源显示名覆盖配置，例如: {\"荐片[优](DS)\": {\"enabled\": false}}",
                    "drpy_sources": {},
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {}


def _default_enabled(name: str) -> bool:
    """默认启用策略：影视点播类关键词命中，且不带禁用标签。"""
    if any(tag in name for tag in _DISABLED_TAGS):
        return False
    if any(seg in name for seg in _DISABLED_NAMES):
        return False
    if any(h in name for h in _ADULT_HINTS):
        return False
    # 盘/网盘搜索类源：只有配置了网盘凭据才启用（夸克/UC/百度/阿里等）
    if "[盘]" in name or "[搜]" in name:
        return _has_cloud_credentials()
    return any(kw in name for kw in _MOVIE_HINTS)


def _has_cloud_credentials() -> bool:
    """检查 drpyS config/env.json 是否配置了任一网盘凭据。"""
    try:
        p = os.path.join(cfg.DRPYS_DIR, "config", "env.json")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = (
            "quark_cookie", "quark_token_cookie", "uc_cookie", "uc_token_cookie",
            "baidu_cookie", "ali_token", "ali_refresh_token", "pikpak_token",
            "xun_username", "xun_password",
        )
        return any(str(data.get(k) or "").strip() for k in keys)
    except Exception:
        return False


def _extract_js_headers(key: str, stype: int) -> dict:
    """从 drpyS 爬虫脚本中提取静态请求头（如荐片 App 专属 UA）。
    仅解析简单的 headers: { 'Key': 'value' } 字面量。"""
    try:
        module = key.split("_", 1)[1] if "_" in key else key
        if stype == 4:
            path = os.path.join(cfg.DRPYS_DIR, "spider", "js", module + ".js")
        elif stype == 3 and key.startswith("catvod_"):
            path = os.path.join(cfg.DRPYS_DIR, "spider", "catvod", module + ".js")
        else:
            return {}
        if not os.path.exists(path):
            return {}
        src = open(path, "r", encoding="utf-8", errors="replace").read()
        m = re.search(r"headers\s*:\s*\{([^}]*)\}", src, re.S)
        if not m:
            return {}
        body = m.group(1)
        headers = {}
        for pair in re.finditer(r"['\"]?([A-Za-z0-9_\-]+)['\"]?\s*:\s*['\"]([^'\"]*)['\"]", body):
            key = pair.group(1).strip()
            val = pair.group(2).strip()
            if key.lower() in ("user-agent", "ua"):
                headers["ua"] = val
            elif key.lower() == "referer":
                headers["referer"] = val
            elif key.lower() in ("origin", "cookie"):
                headers[key.lower()] = val
        return headers
    except Exception:
        return {}


class DrpySource:
    """单个 drpyS 站点（与 MaccmsSource 接口对齐）。"""

    def __init__(self, key: str, name: str, api_url: str, stype: int,
                 ext: str = "", searchable: int = 1, quick_search: int = 0,
                 enabled: bool = True, adult: bool = False,
                 category_map: dict | None = None):
        self.key = key
        self.module = key.split("_", 1)[1] if "_" in key else key
        self.name = name
        self.api_url = api_url
        self.type = stype
        self.ext = ext
        self.searchable = searchable
        self.quick_search = quick_search
        self.enabled = enabled
        self.adult = adult
        self.category_map = category_map or {"movie": "1", "tv": "2", "variety": "3", "anime": "4"}
        self.header_profile = _extract_js_headers(self.key, self.type)
        self.base_url = re.match(r"(https?://[^/]+)", api_url).group(1) if re.match(r"(https?://[^/]+)", api_url) else ""

    # ---------- HTTP ----------

    def _request(self, params: dict | None = None, timeout: int | None = None) -> dict | None:
        """调用 drpyS 标准接口。params 与 TVBox 协议一致：ac/t/pg/wd/ids/play/ep。"""
        allowed = {k: v for k, v in (params or {}).items()
                   if k in ("ac", "t", "pg", "wd", "ids", "play", "ep", "extend", "quick")}
        qs = urllib.parse.urlencode(allowed)
        url = self.api_url + ("&" if "?" in self.api_url else "?") + qs
        if "pwd=" not in url and cfg.DRPYS_API_PWD:
            url += "&pwd=" + urllib.parse.quote(cfg.DRPYS_API_PWD)
        # 模块名/路径可能含中文与 []，urllib 需要百分号编码（保留 URL 结构与已编码参数）
        url = urllib.parse.quote(url, safe=":/?&=%,.-_~")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            })
            with urllib.request.urlopen(req, timeout=timeout or cfg.REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:
            logger.warning(f"[{self.name}] drpyS 请求失败: {e}")
            return None

    # ---------- 公开接口 ----------

    def search(self, keyword: str, timeout: int | None = None) -> list[dict]:
        data = self._request({"ac": "detail", "wd": keyword}, timeout=timeout or cfg.DRPYS_SEARCH_TIMEOUT)
        return self._normalize_list((data or {}).get("list") or [])

    def get_list(self, category: str = "movie", pagesize: int = 100) -> list[dict]:
        return self.list_page(category, 1, pagesize)

    def list_page(self, category: str = "movie", page: int = 1, pagesize: int = 100) -> list[dict]:
        cat_ids = self._split_cat_ids(self.category_map.get(category))
        items = []
        for cid in cat_ids:
            data = self._request({"ac": "detail", "t": cid, "pg": page})
            if data:
                items.extend(data.get("list") or [])
        seen = set()
        uniq = []
        for i in items:
            vid = i.get("vod_id")
            if vid is not None and vid in seen:
                continue
            if vid is not None:
                seen.add(vid)
            uniq.append(i)
        normalized = self._normalize_list(uniq[:pagesize])
        # 分类接口按请求的分类返回，直接标注类型（避免剧名不含关键词被误判为电影）
        for it in normalized:
            it["type"] = category
        return normalized

    def get_detail(self, url_or_id: str) -> tuple[dict, list[dict]]:
        vod_id = self._extract_id(url_or_id)
        if not vod_id:
            return {}, []
        data = self._request({"ac": "detail", "ids": vod_id})
        if not data:
            import time as _t
            _t.sleep(0.8)
            data = self._request({"ac": "detail", "ids": vod_id})
        items = (data or {}).get("list") or []
        if not items:
            return {}, []
        return self._normalize_detail(items[0])

    def get_play_url(self, url_or_id: str) -> str | None:
        lines = self.resolve_play_lines(url_or_id, None, 1)
        if lines:
            return lines[0]["url"]
        _, episodes = self.get_detail(url_or_id)
        if episodes:
            return episodes[0].get("play_url")
        if self._is_media_url(url_or_id):
            return url_or_id
        return url_or_id

    def get_play_candidates(self, url_or_id: str, episode: int = None,
                            max_candidates: int = 6) -> list[str]:
        """返回该视频所有播放线路的指定集地址（默认第一集）。"""
        ep_idx = max(0, int(episode) - 1) if episode else 0
        if not isinstance(ep_idx, int):
            ep_idx = 0
        vod_id = self._extract_id(url_or_id)
        if not vod_id:
            return []
        data = self._request({"ac": "detail", "ids": vod_id})
        items = (data or {}).get("list") or []
        if not items:
            return []
        raw = items[0].get("vod_play_url", "") or ""
        urls = []
        for source_str in re.split(r"\${3,}", raw):
            parts = [p.strip() for p in source_str.split("#") if p.strip()]
            if not parts:
                continue
            ep_part = parts[ep_idx] if ep_idx < len(parts) else parts[-1]
            m = re.match(r".+?\$(https?://[^\$]+)", ep_part)
            url = m.group(1) if m else ep_part
            if url and url.startswith("http") and url not in urls:
                urls.append(url)
            if len(urls) >= max_candidates:
                break
        return urls

    def resolve_lazy(self, url_or_id: str, episode: int = None,
                     max_candidates: int = 6, timeout: int = 12) -> list[dict]:
        """调用 drpyS lazy（play 接口）解析真实播放地址。
        返回 [{"url": str, "header": dict}]；会剥离 #isVideo##fastPlayMode 等播放器标记，
        跳过 127.0.0.1 内部代理地址与 push:// 分享链接（当前不支持）。
        """
        vod_id = self._extract_id(url_or_id)
        if not vod_id:
            return []
        detail = self._request({"ac": "detail", "ids": vod_id}, timeout=timeout)
        items = (detail or {}).get("list") or []
        if not items:
            return []
        flags = [f.strip() for f in (items[0].get("vod_play_from") or "").split("$$$") if f.strip()]
        if not flags:
            return []
        results = []
        for flag in flags[:3]:
            try:
                play = self._request({
                    "play": flag, "ids": vod_id, "ep": episode or 1,
                }, timeout=timeout)
            except Exception:
                continue
            if not play:
                continue
            if int(play.get("parse") or 0) == 1:
                # 需要解析器通道（如夸克社），当前暂不支持
                continue
            url_field = play.get("url")
            header = play.get("header") or {}
            if isinstance(header, str):
                try:
                    header = json.loads(header)
                except Exception:
                    header = {}
            urls = []
            if isinstance(url_field, list):
                # 成对 [名称, url, 名称, url...]
                for u in url_field:
                    if isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
            elif isinstance(url_field, str) and url_field.startswith("http"):
                urls.append(url_field)
            for u in urls:
                # 剥离播放器标记
                clean = re.split(r"#isVideo=true", u, flags=re.I)[0].strip()
                if not clean.startswith(("http://", "https://")):
                    continue
                if "127.0.0.1" in urllib.parse.urlparse(clean).netloc:
                    continue
                results.append({"url": clean, "header": dict(header)})
                if len(results) >= max_candidates:
                    return results
        return results

    def resolve_play_lines(self, url_or_id: str, episode: int = None,
                           max_candidates: int = 6) -> list[dict]:
        """供播放链使用：优先 lazy 直链（盘源必需），退回 vod_play_url 线路。
        返回 [{"url": str, "header": dict}]。"""
        lazy = self.resolve_lazy(url_or_id, episode, max_candidates)
        if lazy:
            return lazy
        direct = self.get_play_candidates(url_or_id, episode, max_candidates)
        profile = self.header_profile or {}
        return [{"url": u, "header": dict(profile)} for u in direct]

    # ---------- 内部处理 ----------

    def _extract_id(self, url_or_id: str) -> str | None:
        url_or_id = str(url_or_id)
        if url_or_id.startswith("drpy://"):
            m = re.search(r"[?&]ids=([^&]+)", url_or_id)
            if m:
                return urllib.parse.unquote(m.group(1))
        if url_or_id.isdigit():
            return url_or_id
        # 分类接口返回的 id 可能带线路后缀，如 575144@1
        if re.match(r"^\d+(@\d+)?$", url_or_id):
            return url_or_id
        m = re.search(r"/vod/(\d+)", url_or_id)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _is_media_url(url: str) -> bool:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return False
        try:
            from urllib.parse import urlparse
            return urlparse(url).path.lower().endswith((".mp4", ".m3u8", ".flv", ".ts", ".mkv"))
        except Exception:
            return False

    def _normalize_list(self, items: list) -> list[dict]:
        results = []
        for item in items:
            if not item or not item.get("vod_name"):
                continue
            vid = item.get("vod_id")
            results.append({
                "title": item["vod_name"],
                "type": self._infer_type(item),
                "cover": item.get("vod_pic", ""),
                "description": (item.get("vod_content") or "")[:200],
                "year": self._safe_int(item.get("vod_year")),
                "area": item.get("vod_area", ""),
                "director": (item.get("vod_director") or "")[:100],
                "actors": (item.get("vod_actor") or "")[:200],
                "rating": self._safe_float(item.get("vod_score") or item.get("vod_rating")),
                "source": self.name,
                "source_url": f"drpy://{self.module}?ids={urllib.parse.quote(str(vid))}" if vid is not None else "",
                "genre": self._clean_genre(item),
                "hits": self._safe_int(item.get("vod_hits")),
                "hits_week": self._safe_int(item.get("vod_hits_week")),
                "douban_score": self._safe_float(item.get("vod_douban_score")),
                "remarks": item.get("vod_remarks", ""),
            })
        return results

    def _normalize_detail(self, item: dict) -> tuple[dict, list[dict]]:
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
            "source_url": f"drpy://{self.module}?ids={urllib.parse.quote(str(item.get('vod_id')))}",
            "genre": self._clean_genre(item),
            "hits": self._safe_int(item.get("vod_hits")),
            "hits_week": self._safe_int(item.get("vod_hits_week")),
            "douban_score": self._safe_float(item.get("vod_douban_score")),
            "remarks": item.get("vod_remarks", ""),
        }
        return video_info, self._parse_episodes(item)

    def _parse_episodes(self, item: dict) -> list[dict]:
        """解析 vod_play_url：'第1集$url#第2集$url$$$线路2...'（与 MacCMS 同格式）。"""
        play_url = item.get("vod_play_url", "")
        if not play_url:
            return []
        sources = re.split(r"\${3,}", play_url)

        def is_direct(url: str) -> bool:
            return self._is_media_url(url)

        def parse_source(source_str: str) -> list[dict]:
            raw_parts = re.split(r"#+", source_str.strip())
            merged = []
            for p in raw_parts:
                p = p.strip()
                if not p:
                    continue
                if bool(re.search(r"\$https?://", p)) or not merged:
                    merged.append(p)
                else:
                    merged[-1] += "#" + p
            result = []
            for part in merged:
                m = re.match(r"(.+?)\$(https?://[^\$]+)", part)
                if m:
                    title, url = m.group(1).strip(), m.group(2).strip()
                else:
                    m2 = re.match(r"(https?://[^\$]+)\$(.+)", part)
                    if m2:
                        url, title = m2.group(1).strip(), m2.group(2).strip()
                    else:
                        title, url = f"第{len(result) + 1}集", part
                if url and not url.startswith("http"):
                    url = urllib.parse.urljoin(self.base_url, url)
                result.append({
                    "episode_num": len(result) + 1,
                    "episode_title": title,
                    "play_url": url,
                    "is_available": 1,
                })
            return result

        parsed_sources = [s for s in (parse_source(x) for x in sources if x.strip()) if s]
        if not parsed_sources:
            return []
        best = None
        for src in parsed_sources:
            if src and is_direct(src[0].get("play_url", "")):
                if best is None or len(src) > len(best):
                    best = src
        if best:
            return best
        return max(parsed_sources, key=len)

    def _infer_type(self, item: dict) -> str:
        type_name = (item.get("type_name") or "").lower()
        type_id = str(item.get("type_id") or "")
        rev_map = {}
        for k, v in self.category_map.items():
            for cid in self._split_cat_ids(v):
                rev_map[cid] = k
        if type_id in rev_map:
            return rev_map[type_id]
        for vtype, kws in TYPE_KEYWORDS.items():
            for kw in kws:
                if kw in type_name or kw in type_id:
                    return vtype
        title = item.get("vod_name", "")
        for vtype, kws in TYPE_KEYWORDS.items():
            for kw in kws:
                if kw in title:
                    return vtype
        class_name = item.get("vod_class") or ""
        for vtype, kws in TYPE_KEYWORDS.items():
            for kw in kws:
                if kw in class_name:
                    return vtype
        return "movie"

    def _clean_genre(self, item: dict) -> str:
        raw = (item.get("vod_class") or "").strip() or (item.get("type_name") or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"^(大陆|国产|中国|内地|欧美|日本|韩国|港台|香港|台湾|美国|英国|法国|泰国|印度)\s*", "", raw)
        if raw in GENRE_MAP:
            return GENRE_MAP[raw]
        for suf in ("电视剧", "连续剧", "剧场版", "电影", "动漫", "动画", "综艺节目", "剧集", "片", "剧"):
            if raw.endswith(suf):
                cand = raw[: -len(suf)]
                if cand in GENRE_MAP:
                    return GENRE_MAP[cand]
                break
        return raw

    @staticmethod
    def _split_cat_ids(cat_id) -> list:
        if not cat_id:
            return []
        return [c.strip() for c in str(cat_id).split(",") if c.strip()]

    @staticmethod
    def _safe_int(val):
        if not val:
            return None
        try:
            m = re.search(r"(\d{4})", str(val))
            return int(m.group(1)) if m else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val):
        if not val:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None


# ---------- 源注册表 ----------

_REGISTRY_LOCK = threading.Lock()
_REGISTRY = None


class DrpyRegistry:
    def __init__(self):
        self._sources: list[DrpySource] = []
        self._loaded = False

    def refresh(self, force: bool = False) -> bool:
        data = fetch_drpy_config(force=force)
        if not data:
            return False
        overrides = _read_registry()
        used_names = set()
        sources = []
        for site in data.get("sites", []):
            key = site.get("key", "")
            if not key:
                continue
            raw_name = site.get("name") or key
            name = recover_name(raw_name)
            # 与现有 MacCMS 源重名时加后缀，避免播放链/历史判定混淆
            if name in used_names:
                name = name + "(drpy)"
            used_names.add(name)
            ov = overrides.get(name) or overrides.get(key) or {}
            adult = bool(ov.get("adult", any(h in name for h in _ADULT_HINTS)))
            enabled = bool(ov.get("enabled", _default_enabled(name)))
            sources.append(DrpySource(
                key=key,
                name=name,
                api_url=site.get("api", ""),
                stype=int(site.get("type", 3)),
                ext=site.get("ext", "") or "",
                searchable=int(site.get("searchable", 1) or 0),
                quick_search=int(site.get("quickSearch", 0) or 0),
                enabled=bool(enabled),
                adult=bool(adult),
            ))
        self._sources = sources
        self._loaded = True
        # 落盘成人源名单，供 adult.py 热路径读取（避免每次查询触发网络）
        try:
            with open(cfg.DRPY_ADULT_NAMES_FILE, "w", encoding="utf-8") as f:
                json.dump({"names": [s.name for s in sources if s.adult]}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"成人源名单写入失败: {e}")
        logger.info(f"drpyS 源注册表刷新: 共 {len(sources)} 个源, 默认启用 {sum(1 for s in sources if s.enabled)} 个, 成人标记 {sum(1 for s in sources if s.adult)} 个")
        return True

    def get_all(self) -> list[DrpySource]:
        if not self._loaded:
            self.refresh()
        return list(self._sources)

    def get_enabled(self) -> list[DrpySource]:
        return [s for s in self.get_all() if s.enabled]

    def get_by_name(self, name: str) -> DrpySource | None:
        for s in self.get_all():
            if s.name == name:
                return s
        return None


def get_registry() -> DrpyRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = DrpyRegistry()
        return _REGISTRY


def refresh_registry(force: bool = False) -> bool:
    return get_registry().refresh(force=force)
