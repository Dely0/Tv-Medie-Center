"""成人内容开关与源配置。

默认关闭。想在自己电脑上开启时，创建/编辑 data/adult_config.json：
{
  "enabled": true,
  "sources": [
    {"name": "成人源A", "base_url": "https://lbapi9.com", "category_map": {...}},
    {"name": "成人源B", "base_url": "http://fhapi9.com", "category_map": {...}}
  ]
}
未创建配置文件时使用内置默认源（处于关闭状态，不会加载）。
开启后顶部导航会出现“成人”页面；关闭时该页面隐藏且源不参与搜索/爬取。
"""
import json
import logging
import os
import time

logger = logging.getLogger("adult")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "adult_config.json")

DEFAULT_SOURCES = [
    {
        "name": "成人源A",
        "base_url": "https://lbapi9.com",
        "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
    },
    {
        "name": "成人源B",
        "base_url": "http://fhapi9.com",
        "category_map": {"movie": "1", "tv": "2", "variety": "3", "anime": "4"},
    },
]

# 普通源里混入的成人/情色内容标题关键词（用于全局隔离，避免误入首页/历史/搜索）
# 来源：公开中文敏感词库（Sensitive-lexicon）+ AV 类型词 + 实际数据补充
# 已排除高误伤词：激情/写真/丝袜/美腿/按摩/性感/咪咪/内裤 等
ADULT_KEYWORDS = [
    # 性行为/身体
    "性爱", "性交", "做爱", "内射", "口交", "乳交", "肛交", "巨乳",
    "射精", "口爆", "口活", "口射", "口淫", "舔阴", "颜射", "潮吹", "潮喷",
    "抽插", "后庭", "拳交", "足交", "脚交", "自慰", "手淫", "春药", "迷药",
    "迷奸药", "迷情药", "轮奸", "强奸", "诱奸", "群交", "乱交", "乱伦", "兽交",
    "人兽", "鸡奸", "淫乱", "淫水", "淫液", "淫穴", "蜜穴", "粉穴", "菊穴",
    "后穴", "肉穴", "小穴", "骚穴", "美穴", "玉穴", "密穴", "阴户", "阴蒂",
    "阴唇", "阴核", "阴道", "阴茎", "阳具", "龟头", "肉棍", "肉茎", "巨屌",
    "鸡巴", "奶子", "巨奶", "豪乳", "爆乳", "大乳", "美乳", "玉乳", "乳沟",
    "乳头", "抓胸", "摸胸", "摸奶", "揉乳", "胸推", "脱光", "裸露", "赤裸",
    "裸照", "一丝不挂", "脱内裤", "原味内衣", "情趣用品", "按摩棒", "精液",
    "肉棒", "无码", "有码", "18禁", "色情", "情色", "AV女优", "AV无码", "Nude",
    # 性癖/题材
    "调教", "捆绑", "凌辱", "性虐", "性奴", "人妻", "熟女", "熟妇", "熟母",
    "少妇", "淫妻", "换妻", "荡妇", "浪女", "浪妇", "骚妇", "骚女", "妓女",
    "应召", "买春", "招妓", "招鸡", "包二奶", "偷欢", "盗撮", "放尿", "痴汉",
    "中出", "艳谭", "艳史", "艳谈", "欲女", "镜花风月", "一路向西", "魔鬼天使",
    "肮脏", "福利姬", "援交", "包养", "陪睡", "黑料", "啪啪", "浪叫",
    "操我", "操死", "狂操", "狂干", "干我", "猛干", "高潮", "偷拍", "大胸",
    "嫩穴", "爆射", "底裤", "迷奸", "约炮", "嫩模", "泄密流出", "勃起",
    "罩杯", "女大学生", "女大生", "私密档案",
    "肥臀", "肉臀", "抖M", "蜜桃臀", "腰臀比", "后入", "爆乳",
    "百合族", "已婚妇女",
    "处女膜", "肉蒲团", "玉女心经", "极乐宝鉴", "灯草和尚", "赤裸羔羊",
    "玉蒲团", "剑奴", "裸体", "全裸", "裸聊",
    # 成人内容类型/平台/片商
    "黄片", "三级片", "色情片", "色情电影", "成人电影", "成人片", "成人网站",
    "无修正", "一本道", "夜勤病栋", "少年阿宾", "风月大陆", "花花公子",
    "麻豆", "天美传媒", "精东", "SWAG", "OnlyFans",
    # 知名成人/写真艺人
    "仓井空", "松岛枫", "杨思敏", "汤加丽", "张筱雨", "夏川纯",
    # 严重犯罪内容（涉未成年/暴力性内容，必须隔离）
    "幼女", "幼交", "美幼", "轮暴", "暴奸",
    # 英文
    "FUCK", "fuck", "SEX", "Sex", "Porn", "porn", "MILF", "milf", "Blowjob",
    "blowjob", "Creampie", "creampie", "Gangbang", "gangbang", "Bondage",
    "bondage", "BDSM", "bdsm", "Anal", "anal",
    # 成人影片番号前缀（片商代码，如 MDHG-0008 / DASS595 / HEYZO-3090）
    "MDHG", "HEYZO", "DASS", "HONB", "BLXC", "KANBi", "REBD", "STARS", "SSIS",
    "SONE", "IPX", "ABP", "MIDE", "MIDV", "MEYD", "JUY", "NTR", "HMN", "FSDSS",
    "PRED", "WANZ", "JUL", "SAME", "CAWD", "ADN", "GVH", "POKA", "CESD", "SIVR",
    # 实际数据补充
    "梅洛迪", "希娜", "Meru", "Melody",
]


def load_config() -> dict:
    """读取成人配置；文件不存在或异常时返回默认关闭配置"""
    data = {"enabled": False, "sources": DEFAULT_SOURCES}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            data["enabled"] = bool(user.get("enabled", False))
            if isinstance(user.get("sources"), list) and user["sources"]:
                data["sources"] = user["sources"]
    except Exception as e:
        logger.warning(f"成人配置读取失败，使用默认: {e}")
    return data


def is_enabled() -> bool:
    return bool(load_config().get("enabled"))


def get_adult_sources() -> list[dict]:
    """开启时返回成人源配置列表；关闭时返回空"""
    cfg = load_config()
    if not cfg.get("enabled"):
        return []
    return cfg.get("sources") or []


def source_names() -> list[str]:
    return [s.get("name", "") for s in get_adult_sources() if s.get("name")]


def known_source_names() -> list[str]:
    """所有已知成人源名（无论开关状态）。

    用于全局隔离：即使配置已关闭，库里残留的成人内容也必须从
    首页/分类/搜索/历史中排除，避免误入家人视野。
    """
    cfg = load_config()
    names = []
    for s in list(cfg.get("sources") or []) + list(DEFAULT_SOURCES):
        n = s.get("name")
        if n and n not in names:
            names.append(n)
    # 附加：drpyS 生态中自动识别/手动标记的成人源（读运行时缓存，无网络）
    try:
        import config as _app_cfg
        with open(_app_cfg.DRPY_ADULT_NAMES_FILE, "r", encoding="utf-8") as f:
            extra = json.load(f).get("names") or []
        for n in extra:
            if n and n not in names:
                names.append(n)
    except Exception:
        pass
    return names


def adult_keywords() -> list[str]:
    return list(ADULT_KEYWORDS)


def is_adult_title(title: str) -> bool:
    """按标题关键词判断是否成人/情色内容（覆盖普通源里混入的内容）"""
    if not title:
        return False
    t = str(title)
    if any(k in t for k in ADULT_KEYWORDS):
        return True
    # 成人影片番号：独立词边界的 2-8个字母 + 连字符/下划线/无分隔 + 3-5位数字
    # （如 MDHG-0008、DASS595；词边界避免截断长单词如 SEVENTEEN2021）
    import re
    return bool(re.search(r"\b[A-Za-z]{2,8}[-_]?\d{3,5}\b", t))


def adult_cond_sql(column: str = "v") -> tuple[str, list]:
    """生成“成人内容”判定 SQL：来源属于成人源 或 标题含成人关键词。
    返回 (SQL片段, 参数)。column 用于限定表别名（如 v / videos）。"""
    names = known_source_names()
    parts = []
    params = []
    if names:
        parts.append(f"{column}.source IN ({','.join('?' * len(names))})")
        params.extend(names)
    for kw in ADULT_KEYWORDS:
        parts.append(f"{column}.title LIKE ?")
        params.append(f"%{kw}%")
    # 番号模式：大写字母(2+) - 数字(3+)（如 MDHG-0008），SQLite GLOB 匹配
    parts.append(f"{column}.title GLOB '*[A-Z][A-Z]*-[0-9][0-9][0-9]*'")
    if not parts:
        return "", []
    return "(" + " OR ".join(parts) + ")", params


_sync_state = {"running": False, "count": 0, "last_run": None, "error": None}


def sync_adult_content(pages_per_category: int = 2) -> int:
    """轻量回填成人源列表页到本地库（后台调用，约 1-2 分钟）"""
    if _sync_state["running"]:
        return 0
    _sync_state["running"] = True
    _sync_state["error"] = None
    total = 0
    try:
        # 函数内局部导入，避免与 maccms_source 循环引用
        from app.database import upsert_video
        from app.maccms_source import MaccmsSource
        seen = set()
        for item in get_adult_sources():
            src = MaccmsSource(
                name=item.get("name", "成人源"),
                base_url=item.get("base_url", ""),
                category_map=item.get("category_map"),
            )
            for cat in ("movie", "tv", "variety", "anime"):
                for pg in range(1, pages_per_category + 1):
                    try:
                        items = src.list_page(cat, pg, pagesize=60)
                    except Exception:
                        continue
                    if not items:
                        break
                    for it in items:
                        su = it.get("source_url", "")
                        if not su or su in seen:
                            continue
                        seen.add(su)
                        try:
                            upsert_video(it)
                            total += 1
                        except Exception:
                            pass
        _sync_state["count"] = total
        _sync_state["last_run"] = time.time()
        logger.info(f"成人内容同步完成: {total} 条")
    except Exception as e:
        _sync_state["error"] = str(e)
        logger.warning(f"成人内容同步失败: {e}")
    finally:
        _sync_state["running"] = False
    return total


def sync_status() -> dict:
    return dict(_sync_state)
