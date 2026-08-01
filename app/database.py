"""SQLite 数据库模块"""
import sqlite3
import json
import logging
import os
import re
import random
import datetime
import threading
from collections import Counter
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger("database")

_local = threading.local()


def _adult_exclude_sql(column: str = "videos") -> tuple[str, list]:
    """返回排除成人内容的 SQL 片段与参数（来源属于成人源 或 标题含成人关键词）。

    无论开关状态始终生效，保证成人内容不进入首页/分类/历史。
    column 为表别名（如 'videos' 或 'v'），避免与 FTS 虚拟表列名歧义。
    """
    from app.adult import adult_cond_sql
    cond, params = adult_cond_sql(column)
    if not cond:
        return "", []
    return f" AND NOT {cond}", params


def get_conn() -> sqlite3.Connection:
    """每个线程获取独立连接"""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.execute("PRAGMA cache_size=-8000")  # 8MB
    return _local.conn


@contextmanager
def get_db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """初始化数据库表结构"""
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('movie','tv','variety','anime')),
                cover TEXT,
                description TEXT,
                year INTEGER,
                area TEXT,
                director TEXT,
                actors TEXT,
                rating REAL,
                source TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL UNIQUE,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                episode_num INTEGER NOT NULL,
                episode_title TEXT,
                play_url TEXT,
                is_available INTEGER DEFAULT 1,
                UNIQUE(video_id, episode_num)
            );

            CREATE TABLE IF NOT EXISTS watch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                episode_id INTEGER,
                progress_seconds REAL DEFAULT 0,
                total_seconds REAL DEFAULT 0,
                watched_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0
            );

            -- 豆瓣热播榜同步结果（供首页热播/分类排序/推荐使用）
            CREATE TABLE IF NOT EXISTS douban_ranks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                rank INTEGER NOT NULL,
                video_id INTEGER,
                douban_id TEXT,
                title TEXT,
                score REAL DEFAULT 0,
                score_count INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- 推荐池（后台任务构建：常看标签→豆瓣推荐→源站匹配，首页只读，保证加载速度）
            CREATE TABLE IF NOT EXISTS recommend_pool (
                video_id INTEGER PRIMARY KEY,
                score REAL DEFAULT 0,
                reason TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_videos_type ON videos(type);
            CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);
            CREATE INDEX IF NOT EXISTS idx_videos_updated ON videos(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_video ON episodes(video_id);
            CREATE INDEX IF NOT EXISTS idx_history_watched ON watch_history(watched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_douban_ranks_video ON douban_ranks(video_id);
            CREATE INDEX IF NOT EXISTS idx_douban_ranks_cat ON douban_ranks(category, rank);
            CREATE INDEX IF NOT EXISTS idx_recommend_pool_score ON recommend_pool(score DESC);
        """)
        _ensure_columns(db)
        _init_fts(db)


# trigram 分词器支持中文子串匹配（SQLite >= 3.34），否则回退 unicode61 + LIKE
FTS_TOKENIZER = "trigram" if sqlite3.sqlite_version_info >= (3, 34, 0) else "unicode61"


def _ensure_columns(db: sqlite3.Connection):
    """幂等补充 videos 表新增列（排行/题材/备注）"""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(videos)").fetchall()}
    columns = {
        "genre": "TEXT",
        "hits": "INTEGER DEFAULT 0",
        "hits_week": "INTEGER DEFAULT 0",
        "douban_score": "REAL",
        "remarks": "TEXT",
    }
    for col, ddl in columns.items():
        if col not in existing:
            db.execute(f"ALTER TABLE videos ADD COLUMN {col} {ddl}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_videos_genre ON videos(genre)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_videos_hits_week ON videos(hits_week)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_videos_douban_score ON videos(douban_score)")


def _init_fts(db: sqlite3.Connection):
    """创建 FTS5 表与同步触发器；旧库（unicode61）自动迁移为 trigram 并重建索引。"""
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='videos_fts'"
    ).fetchone()
    migrated = False
    if row and FTS_TOKENIZER not in row[0]:
        # 旧 tokenizer 无法中文子串搜索：先删触发器，再重建表
        db.executescript("""
            DROP TRIGGER IF EXISTS videos_ai;
            DROP TRIGGER IF EXISTS videos_ad;
            DROP TRIGGER IF EXISTS videos_au;
            DROP TABLE IF EXISTS videos_fts;
        """)
        migrated = True

    db.executescript(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
            title, description, director, actors,
            content='videos',
            content_rowid='id',
            tokenize='{FTS_TOKENIZER}'
        );

        -- 触发器：保持 FTS 同步
        CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
            INSERT INTO videos_fts(rowid, title, description, director, actors)
            VALUES (new.id, new.title, new.description, new.director, new.actors);
        END;

        CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
            INSERT INTO videos_fts(videos_fts, rowid, title, description, director, actors)
            VALUES ('delete', old.id, old.title, old.description, old.director, old.actors);
        END;

        CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE ON videos BEGIN
            INSERT INTO videos_fts(videos_fts, rowid, title, description, director, actors)
            VALUES ('delete', old.id, old.title, old.description, old.director, old.actors);
            INSERT INTO videos_fts(rowid, title, description, director, actors)
            VALUES (new.id, new.title, new.description, new.director, new.actors);
        END;
    """)
    if migrated:
        db.execute("INSERT INTO videos_fts(videos_fts) VALUES('rebuild')")


# ─── 查询方法 ───

def search_videos(keyword: str, page: int = 1, page_size: int = 30,
                  include_adult: bool = False) -> tuple[list[dict], int]:
    """搜索视频：trigram 支持中文子串；短词/特殊字符回退 LIKE。
    include_adult=True 时（成人开关开启）允许返回成人内容，否则始终排除。"""
    offset = (page - 1) * page_size
    match_q = None
    with get_db() as db:
        total = 0
        # trigram 至少需要 3 个字符，短词直接用 LIKE
        if len(keyword) >= 3:
            # 用短语查询避免关键字中的 FTS 运算符（引号、冒号等）导致语法错误
            match_q = '"' + keyword.replace('"', '""') + '"'
            try:
                excl_sql, excl_params = ("" , []) if include_adult else _adult_exclude_sql("v")
                count_row = db.execute(
                    "SELECT COUNT(*) FROM videos_fts fts JOIN videos v ON v.id = fts.rowid "
                    f"WHERE fts MATCH ?{excl_sql}",
                    (match_q,) + tuple(excl_params)
                ).fetchone()
                total = count_row[0] if count_row else 0
            except Exception:
                total = 0

        if total > 0:
            rows = db.execute(
                f"""
                SELECT v.* FROM videos v, videos_fts fts
                WHERE v.id = fts.rowid AND videos_fts MATCH ?{excl_sql}
                ORDER BY v.updated_at DESC
                LIMIT ? OFFSET ?
                """, (match_q,) + tuple(excl_params) + (page_size, offset)
            ).fetchall()
        else:
            # FTS 不匹配（短词/特殊字符场景），用 LIKE 保底
            like = f"%{keyword}%"
            excl_sql, excl_params = ("", []) if include_adult else _adult_exclude_sql("videos")
            count_row = db.execute(
                f"SELECT COUNT(*) FROM videos WHERE (title LIKE ? OR description LIKE ? OR actors LIKE ? OR director LIKE ?){excl_sql}",
                (like, like, like, like) + tuple(excl_params)
            ).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                f"""SELECT * FROM videos
                   WHERE (title LIKE ? OR description LIKE ? OR actors LIKE ? OR director LIKE ?){excl_sql}
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (like, like, like, like) + tuple(excl_params) + (page_size, offset)
            ).fetchall()

    return [dict(r) for r in rows], total


def get_videos_by_type(type_: str, page: int = 1, page_size: int = 30,
                       genre: str = "", area: str = "", year: str = "") -> tuple[list[dict], int]:
    """按类型分页查询；type_='recent' 按更新时间不分类型；type_='adult' 按成人源过滤；
    genre/area/year 非空时按维度过滤"""
    offset = (page - 1) * page_size
    conds, params = [], []
    if genre:
        conds.append("genre LIKE ?")
        params.append(f"%{genre}%")
    if area:
        conds.append(_area_sql(area))
    if year:
        conds.append(_year_sql(year))
    with get_db() as db:
        if type_ == "recent":
            excl_sql, excl_params = _adult_exclude_sql()
            where = (" WHERE " + " AND ".join(conds) + excl_sql) if (conds or excl_sql) else ""
            params_all = params + excl_params
            count_row = db.execute(f"SELECT COUNT(*) FROM videos{where}", params_all).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                f"SELECT * FROM videos{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params_all + [page_size, offset]
            ).fetchall()
        elif type_ == "adult":
            from app.adult import source_names
            names = source_names()
            if not names:
                return [], 0
            conds_all = [f"source IN ({','.join('?' * len(names))})"] + conds
            params_all = list(names) + params
            where = " WHERE " + " AND ".join(conds_all)
            count_row = db.execute(f"SELECT COUNT(*) FROM videos{where}", params_all).fetchone()
            total = count_row[0] if count_row else 0
            order_by = (
                "ORDER BY "
                "(EXISTS(SELECT 1 FROM douban_ranks r WHERE r.video_id = videos.id)) DESC, "
                "COALESCE(NULLIF(douban_score,0), NULLIF(rating,0)) DESC, "
                "updated_at DESC"
            )
            rows = db.execute(
                f"SELECT * FROM videos{where} {order_by} LIMIT ? OFFSET ?",
                params_all + [page_size, offset]
            ).fetchall()
        else:
            conds_all = ["type=?"] + conds
            excl_sql, excl_params = _adult_exclude_sql()
            if excl_sql:
                conds_all.append(excl_sql[5:])  # 去掉前缀 " AND "
            params_all = [type_] + params + excl_params
            where = " WHERE " + " AND ".join(conds_all)
            count_row = db.execute(f"SELECT COUNT(*) FROM videos{where}", params_all).fetchone()
            total = count_row[0] if count_row else 0
            order_by = (
                "ORDER BY "
                "(EXISTS(SELECT 1 FROM douban_ranks r WHERE r.video_id = videos.id AND r.category = ?)) DESC, "
                "COALESCE(NULLIF(douban_score,0), NULLIF(rating,0)) DESC, "
                "updated_at DESC"
            )
            rows = db.execute(
                f"SELECT * FROM videos{where} {order_by} LIMIT ? OFFSET ?",
                params_all + [type_] + [page_size, offset]
            ).fetchall()
    return [dict(r) for r in rows], total


def get_genres(type_: str) -> dict:
    """某分类下的筛选维度：tags(题材) / areas(地区) / years(年份)，各含数量"""
    result = {"tags": [], "areas": [], "years": []}
    excl_sql, excl_params = _adult_exclude_sql()
    with get_db() as db:
        # 题材：拆分组合标签后统计
        rows = db.execute(
            f"SELECT genre FROM videos WHERE type=? AND genre != ''{excl_sql}",
            (type_,) + tuple(excl_params)
        ).fetchall()
        tag_counter = Counter()
        for (g,) in rows:
            for part in re.split(r"[,，、/|·\s]+", g):
                part = part.strip()
                if part and part not in ("高清", "1080P", "4K", "蓝光", "超清", "完整版"):
                    tag_counter[part] += 1
        result["tags"] = [
            {"key": t, "label": t, "count": c}
            for t, c in tag_counter.most_common(24)
        ]

        # 地区：按归一化分组统计
        rows = db.execute(
            f"SELECT area FROM videos WHERE type=? AND area != ''{excl_sql}",
            (type_,) + tuple(excl_params)
        ).fetchall()
        area_counter = Counter()
        for (a,) in rows:
            key = _normalize_area(a)
            if key:
                area_counter[key] += 1
        area_order = ["国产", "港台", "日韩", "欧美", "泰国", "印度", "其他"]
        result["areas"] = sorted(
            [{"key": k, "label": k, "count": c} for k, c in area_counter.items()],
            key=lambda x: area_order.index(x["key"]) if x["key"] in area_order else 99,
        )

        # 年份：具体年份 + 年代分组
        rows = db.execute(
            f"SELECT year FROM videos WHERE type=? AND year IS NOT NULL{excl_sql}",
            (type_,) + tuple(excl_params)
        ).fetchall()
        year_counter = Counter()
        for (y,) in rows:
            key = _year_group(y)
            if key:
                year_counter[key] += 1
        year_items = [{"key": k, "label": k, "count": c} for k, c in year_counter.items()]
        decade_rank = {"2000年代": 0, "90年代": 1, "80年代": 2, "更早": 3}
        result["years"] = sorted(
            year_items,
            key=lambda x: (0, -int(x["key"])) if x["key"].isdigit()
            else (1, decade_rank.get(x["key"], 9)),
        )
    return result


# ─── 分类维度：地区 / 年份 ───

_AREA_GROUPS = {
    "国产": ["中国大陆", "中国内地", "大陆", "内地", "中国"],
    "港台": ["香港", "台湾", "澳门"],
    "日韩": ["日本", "韩国", "日韩"],
    "欧美": [
        "美国", "英国", "法国", "德国", "意大利", "西班牙", "加拿大", "俄罗斯",
        "澳大利亚", "巴西", "墨西哥", "阿根廷", "波兰", "挪威", "苏联", "葡萄牙",
        "荷兰", "瑞典", "丹麦", "比利时", "瑞士", "爱尔兰", "奥地利", "芬兰",
        "希腊", "捷克", "乌克兰", "新西兰",
    ],
    "泰国": ["泰国"],
    "印度": ["印度"],
}
_AREA_ORDER = ["国产", "港台", "日韩", "欧美", "泰国", "印度"]


def _normalize_area(area: str) -> str:
    """把地区字段归一到分组标签；无法识别时返回'其他'"""
    a = area or ""
    for group, keywords in _AREA_GROUPS.items():
        if any(k in a for k in keywords):
            return group
    return "其他"


def _area_sql(key: str) -> str:
    """地区筛选 SQL 片段（与 _normalize_area 的映射保持一致）"""
    if key not in _AREA_GROUPS and key != "其他":
        return "1=0"  # 未知名目直接过滤为空
    if key == "其他":
        positives = " OR ".join(f"area LIKE '%{k}%'" for kw in _AREA_GROUPS.values() for k in kw)
        return f"(area != '' AND NOT ({positives}))"
    keywords = _AREA_GROUPS[key]
    return "(" + " OR ".join(f"area LIKE '%{k}%'" for k in keywords) + ")"


def _year_group(year) -> str | None:
    """年份 → 筛选项；具体年份只保留 2017~今年，未来脏数据忽略，其余归入年代分组"""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    import datetime
    current_year = datetime.date.today().year
    if 2017 <= y <= current_year:
        return str(y)
    if y >= 2000:
        return "2000年代"
    if y >= 1990:
        return "90年代"
    if y >= 1980:
        return "80年代"
    return "更早"


def _year_sql(key: str) -> str:
    """年份筛选 SQL 片段"""
    if key.isdigit():
        return f"year={int(key)}"
    if key == "2000年代":
        return "year BETWEEN 2000 AND 2016"
    if key == "90年代":
        return "year BETWEEN 1990 AND 1999"
    if key == "80年代":
        return "year BETWEEN 1980 AND 1989"
    if key == "更早":
        return "year < 1980"
    return "year IS NULL"


def get_video_detail(video_id: int) -> dict | None:
    """获取视频详情"""
    with get_db() as db:
        video = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not video:
            return None
        episodes = db.execute(
            "SELECT * FROM episodes WHERE video_id=? ORDER BY episode_num ASC",
            (video_id,)
        ).fetchall()
    result = dict(video)
    result["episodes"] = [dict(e) for e in episodes]
    return result


def get_home_data() -> dict:
    """首页数据：Hero（继续观看/最近更新精选）+ 去重后的栏目"""
    excl_sql, excl_params = _adult_exclude_sql()
    with get_db() as db:
        hero = _get_home_hero(db)
        seen = set()

        # 先按“保留优先级”占用去重集合（榜单优先，避免被其他栏目挤掉），再按展示顺序输出
        def take(rows, limit=20):
            items = []
            for r in rows:
                if len(items) >= limit:
                    break
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                items.append(dict(r))
            return items

        reserved = {}
        reserved["score"] = take(db.execute(
            f"""SELECT * FROM videos
               WHERE (douban_score > 0 OR rating > 0){excl_sql}
               ORDER BY COALESCE(NULLIF(douban_score, 0), NULLIF(rating, 0)) DESC, updated_at DESC
               LIMIT 40""",
            excl_params
        ).fetchall())
        reserved["hot"] = take(db.execute(
            f"""SELECT v.* FROM douban_ranks r
               JOIN videos v ON v.id = r.video_id
               WHERE 1=1{_adult_exclude_sql('v')[0]}
               ORDER BY r.rank ASC, v.updated_at DESC
               LIMIT 60""",
            _adult_exclude_sql('v')[1]
        ).fetchall())
        if len(reserved["hot"]) < 20:
            # 豆瓣榜不足时用源站热度/站内观看/高分混合兜底
            reserved["hot"].extend(take(db.execute(
                f"""SELECT v.*, COUNT(h.id) AS local_views
                   FROM videos v LEFT JOIN watch_history h ON h.video_id = v.id
                   WHERE (v.hits_week > 0 OR v.hits > 0 OR v.douban_score > 0 OR v.rating > 0){_adult_exclude_sql('v')[0]}
                   GROUP BY v.id
                   ORDER BY
                     (COALESCE(v.hits_week,0)*100 + COALESCE(v.hits,0)*20 + COUNT(h.id)*250) DESC,
                     COALESCE(NULLIF(v.douban_score,0), NULLIF(v.rating,0)) DESC,
                     v.updated_at DESC
                   LIMIT 60""",
                _adult_exclude_sql('v')[1]
            ).fetchall(), 20 - len(reserved["hot"])))
        reserved["recommend"] = take(get_recommendations(20))
        reserved["recent"] = take(db.execute(
            f"SELECT * FROM videos WHERE 1=1{excl_sql} ORDER BY updated_at DESC LIMIT 40",
            excl_params
        ).fetchall())
        reserved["cats"] = {}
        label_map = {"movie": "电影", "tv": "电视剧", "variety": "综艺", "anime": "动漫"}
        for type_ in ["movie", "tv", "variety", "anime"]:
            reserved["cats"][type_] = take(db.execute(
                f"SELECT * FROM videos WHERE type=?{excl_sql} ORDER BY updated_at DESC LIMIT 60",
                (type_,) + tuple(excl_params)
            ).fetchall())

        sections = []
        if reserved["recommend"]:
            sections.append({"name": "为你推荐", "type": "recommend", "videos": reserved["recommend"]})
        if reserved["hot"]:
            sections.append({"name": "热播榜", "type": "hot", "videos": reserved["hot"]})
        if reserved["score"]:
            sections.append({"name": "高分榜", "type": "score", "videos": reserved["score"]})
        sections.append({"name": "最近更新", "type": "recent", "videos": reserved["recent"]})
        for type_ in ["movie", "tv", "variety", "anime"]:
            if reserved["cats"][type_]:
                sections.append({"name": label_map[type_], "type": type_, "videos": reserved["cats"][type_]})

    return {"hero": hero, "sections": sections}


def _get_home_hero(db) -> dict | None:
    """最近观看记录（有进度）作为继续观看；否则取最近更新精选"""
    excl_sql, excl_params = _adult_exclude_sql("v")
    row = db.execute(
        f"""SELECT h.video_id, h.episode_id, h.progress_seconds, h.total_seconds,
                  v.title, v.cover, v.type
           FROM watch_history h
           JOIN videos v ON v.id = h.video_id
           WHERE 1=1{excl_sql}
           ORDER BY h.watched_at DESC, h.id DESC
           LIMIT 1""",
        excl_params
    ).fetchone()
    if row and (row["progress_seconds"] or 0) > 0:
        episode_num = row["episode_id"]
        episode_title = ""
        if episode_num is not None:
            ep = db.execute(
                "SELECT episode_title FROM episodes WHERE video_id=? AND episode_num=?",
                (row["video_id"], episode_num)
            ).fetchone()
            episode_title = ep["episode_title"] if ep else ""
        return {
            "kind": "continue",
            "video_id": row["video_id"],
            "title": row["title"],
            "cover": row["cover"] or "",
            "type": row["type"],
            "episode_num": episode_num,
            "episode_title": episode_title or "",
            "progress_seconds": float(row["progress_seconds"] or 0),
            "total_seconds": float(row["total_seconds"] or 0),
        }

    recent = db.execute(
        f"SELECT * FROM videos WHERE cover != ''{excl_sql} ORDER BY updated_at DESC LIMIT 1",
        excl_params
    ).fetchone()
    if recent:
        return {
            "kind": "recent",
            "video_id": recent["id"],
            "title": recent["title"],
            "cover": recent["cover"] or "",
            "type": recent["type"],
            "episode_num": None,
            "episode_title": "",
            "progress_seconds": 0,
            "total_seconds": 0,
        }
    return None


def _split_names(text: str) -> list[str]:
    """把演员/导演字符串拆成关键词（兼容中英文逗号、顿号、斜杠）"""
    if not text:
        return []
    return [n.strip() for n in re.split(r"[,\uff0c\u3001/|]+", text) if len(n.strip()) >= 2]


def get_recommendations(limit: int = 8) -> list[dict]:
    """为你推荐：只读推荐池（后台每日构建），保证首页毫秒级返回。

    推荐池由 build_recommend_pool() 在后台任务中构建：
    常看标签 → 豆瓣标签推荐 → 源站匹配入库。无池时回退豆瓣热播高分池。
    最终带“当日种子”轮换（当天稳定，次日变化）。
    """
    excl_sql, excl_params = _adult_exclude_sql("v")
    with get_db() as db:
        rows = db.execute(
            f"""SELECT v.* FROM recommend_pool p
               JOIN videos v ON v.id = p.video_id
               WHERE p.video_id NOT IN (SELECT video_id FROM watch_history){excl_sql}
               ORDER BY p.score DESC, p.updated_at DESC
               LIMIT 200""",
            excl_params
        ).fetchall()
        items = [dict(r) for r in rows]
        if not items:
            rows = db.execute(
                f"""SELECT v.* FROM douban_ranks r
                   JOIN videos v ON v.id = r.video_id
                   WHERE r.score > 0{excl_sql}
                   ORDER BY r.score DESC, r.rank ASC
                   LIMIT 150""",
                excl_params
            ).fetchall()
            items = [dict(r) for r in rows]
    return _seed_rank(items, limit)


def build_recommend_pool(max_tags: int = 2, per_tag: int = 15) -> int:
    """后台任务：按观看历史常看标签 → 豆瓣标签推荐 → 源站匹配入库 → 写推荐池。
    返回池内视频数。网络耗时较长，应在后台线程/每日任务中调用。"""
    with get_db() as db:
        watched = db.execute(
            """SELECT h.video_id, v.genre
               FROM watch_history h
               JOIN videos v ON v.id = h.video_id
               GROUP BY h.video_id
               ORDER BY MAX(h.watched_at) DESC, MAX(h.id) DESC
               LIMIT 10"""
        ).fetchall()
        if not watched:
            return 0
        watched_ids = [r["video_id"] for r in watched]
        tag_counter = Counter()
        for r in watched:
            for part in re.split(r"[,，、/|·\s]+", r["genre"] or ""):
                part = part.strip()
                if part and len(part) >= 2 and part not in ("高清", "1080P", "4K", "蓝光", "超清", "完整版"):
                    tag_counter[part] += 1
        top_tags = [t for t, _ in tag_counter.most_common(max_tags)]
        if not top_tags:
            return 0

    from app.douban import fetch_tag_recommend, normalize_title
    from app.douban import match_in_local_db, search_and_upsert

    entries = {}
    for tag in top_tags:
        try:
            items = fetch_tag_recommend(tag, per_tag)
        except Exception as e:
            logger.warning(f"豆瓣标签推荐[{tag}]失败: {e}")
            continue
        for it in items:
            try:
                vid = match_in_local_db(normalize_title(it["title"]))
                if vid is None:
                    vid = search_and_upsert(it["title"])
            except Exception:
                continue
            if vid and vid not in watched_ids:
                entries[vid] = max(entries.get(vid, 0), float(it["score"] or 0))

    if not entries:
        return 0
    with get_db() as db:
        db.execute("DELETE FROM recommend_pool")
        for vid, score in entries.items():
            db.execute("INSERT INTO recommend_pool (video_id, score) VALUES (?, ?)", (vid, score))
    logger.info(f"推荐池构建完成: {len(entries)} 条")
    return len(entries)


def _seed_rank(items: list[dict], limit: int) -> list[dict]:
    """带当日种子的综合排序：评分 + 标签命中 + 轻微随机轮换（当天稳定，次日变化）"""
    seed = int(datetime.date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    scored = []
    for d in items:
        score = (d.get("_douban_score") or 0) or (d.get("douban_score") or 0) or (d.get("rating") or 0)
        heat = (d.get("hits_week") or 0) * 100 + (d.get("hits") or 0) * 20
        match = d.get("_match") or 0
        total = match * 1.2 + score * 0.6 + heat * 0.0001 + rng.random() * 0.5
        scored.append((total, d))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, d in scored[:limit]:
        d.pop("_match", None)
        d.pop("_source", None)
        d.pop("_douban_score", None)
        out.append(d)
    return out


def get_related(video_id: int, limit: int = 8) -> list[dict]:
    """猜你喜欢：与指定影片同类型 / 同演员 / 同导演，排除自身"""
    with get_db() as db:
        v = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not v:
            return []

        keywords = set(_split_names(v["actors"])) | set(_split_names(v["director"]))
        conds = ["id != ?"]
        params = [video_id]
        match_parts = []
        if v["type"]:
            match_parts.append("type = ?")
            params.append(v["type"])
        for kw in list(keywords)[:20]:
            match_parts.append("(actors LIKE ? OR director LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])
        if not match_parts:
            return []
        conds.append("(" + " OR ".join(match_parts) + ")")
        excl_sql, excl_params = _adult_exclude_sql("videos")
        if excl_sql:
            conds.append(excl_sql[5:])  # 去掉前缀 " AND "

        rows = db.execute(
            f"SELECT * FROM videos WHERE {' AND '.join(conds)} "
            "ORDER BY rating DESC, updated_at DESC LIMIT ?",
            params + excl_params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def get_watch_history(limit: int = 20, adult: bool = False) -> list[dict]:
    """获取观看历史 — 每个视频只返回最新一条记录。
    adult=False（默认）排除成人内容；adult=True 只返回成人内容（供成人页单独展示）。"""
    from app.adult import adult_cond_sql
    cond, params = adult_cond_sql("v")
    if cond:
        if adult:
            cond_sql = f" AND {cond}"
        else:
            cond_sql = f" AND NOT {cond}"
    else:
        cond_sql = ""
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT h.*, v.title, v.cover, v.type, v.source,
                   e.episode_title
            FROM watch_history h
            JOIN videos v ON h.video_id = v.id
            LEFT JOIN episodes e ON h.episode_id = e.episode_num AND e.video_id = h.video_id
            INNER JOIN (
              SELECT video_id, MAX(watched_at) AS max_watched
              FROM watch_history
              GROUP BY video_id
            ) latest ON h.video_id = latest.video_id AND h.watched_at = latest.max_watched
            WHERE 1=1{cond_sql}
            ORDER BY h.watched_at DESC
            LIMIT ?
            """, params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def save_watch_history(video_id: int, episode_id: int | None,
                       progress: float, total: float):
    """保存/更新观看历史 — 同视频同集覆盖, 不重复插入"""
    with get_db() as db:
        if episode_id is not None:
            existing = db.execute(
                "SELECT id FROM watch_history WHERE video_id=? AND episode_id=?",
                (video_id, episode_id)
            ).fetchone()
        else:
            existing = db.execute(
                "SELECT id FROM watch_history WHERE video_id=? AND episode_id IS NULL",
                (video_id,)
            ).fetchone()
        if existing:
            db.execute(
                """UPDATE watch_history
                   SET progress_seconds=?, total_seconds=?, watched_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (progress, total, existing[0])
            )
        else:
            db.execute(
                """INSERT INTO watch_history(video_id, episode_id, progress_seconds, total_seconds)
                   VALUES (?, ?, ?, ?)""",
                (video_id, episode_id, progress, total)
            )


def delete_adult_history() -> int:
    """删除观看历史中的成人内容记录（按来源+标题关键词判定），删除前备份。
    返回删除条数。"""
    from app.adult import adult_cond_sql
    cond, params = adult_cond_sql("v")
    if not cond:
        return 0
    with get_db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS watch_history_adult_backup "
            "AS SELECT * FROM watch_history WHERE 1=0"
        )
        rows = db.execute(
            f"SELECT h.id FROM watch_history h JOIN videos v ON v.id = h.video_id WHERE {cond}",
            params
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            ph = ",".join("?" * len(ids))
            db.execute(
                f"INSERT INTO watch_history_adult_backup SELECT * FROM watch_history WHERE id IN ({ph})",
                ids
            )
            db.execute(f"DELETE FROM watch_history WHERE id IN ({ph})", ids)
        logger.info(f"清理成人观看历史: 删除 {len(ids)} 条（已备份）")
        return len(ids)


def upsert_video(video: dict) -> int:
    """插入或更新视频，返回 video_id"""
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM videos WHERE source_url=?",
            (video["source_url"],)
        ).fetchone()
        if existing:
            # 更新已有记录
            fields = ["title", "type", "cover", "description", "year",
                      "area", "director", "actors", "rating", "source",
                      "genre", "hits", "hits_week", "douban_score", "remarks"]
            sets = ", ".join(f"{f}=?" for f in fields)
            sets += ", updated_at=CURRENT_TIMESTAMP"
            values = [video.get(f) for f in fields]
            values.append(video["source_url"])
            db.execute(
                f"UPDATE videos SET {sets} WHERE source_url=?",
                values
            )
            return existing[0]
        else:
            cur = db.execute(
                """INSERT INTO videos(title, type, cover, description, year,
                   area, director, actors, rating, source, genre, hits, hits_week,
                   douban_score, remarks, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video["title"], video.get("type", "movie"),
                 video.get("cover"), video.get("description"),
                 video.get("year"), video.get("area"),
                 video.get("director"), video.get("actors"),
                 video.get("rating"), video.get("source", ""),
                 video.get("genre", ""), video.get("hits", 0),
                 video.get("hits_week", 0), video.get("douban_score"),
                 video.get("remarks", ""),
                 video["source_url"])
            )
            return cur.lastrowid


def upsert_episode(video_id: int, episode: dict):
    """插入或更新剧集"""
    with get_db() as db:
        db.execute(
            """INSERT INTO episodes(video_id, episode_num, episode_title, play_url, is_available)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(video_id, episode_num) DO UPDATE SET
               episode_title=excluded.episode_title,
               play_url=excluded.play_url,
               is_available=excluded.is_available""",
            (video_id, episode["episode_num"],
             episode.get("episode_title", ""),
             episode.get("play_url"),
             episode.get("is_available", 1))
        )


def rebuild_fts():
    """重建 FTS 索引（爬虫批量导入后调用）"""
    with get_db() as db:
        db.execute("INSERT INTO videos_fts(videos_fts) VALUES('rebuild')")
