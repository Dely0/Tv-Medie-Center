"""SQLite 数据库模块"""
import sqlite3
import json
import os
import re
import threading
from contextlib import contextmanager
from config import DB_PATH

_local = threading.local()


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

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_videos_type ON videos(type);
            CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);
            CREATE INDEX IF NOT EXISTS idx_videos_updated ON videos(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_video ON episodes(video_id);
            CREATE INDEX IF NOT EXISTS idx_history_watched ON watch_history(watched_at DESC);
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

def search_videos(keyword: str, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
    """搜索视频：trigram 支持中文子串；短词/特殊字符回退 LIKE"""
    offset = (page - 1) * page_size
    match_q = None
    with get_db() as db:
        total = 0
        # trigram 至少需要 3 个字符，短词直接用 LIKE
        if len(keyword) >= 3:
            # 用短语查询避免关键字中的 FTS 运算符（引号、冒号等）导致语法错误
            match_q = '"' + keyword.replace('"', '""') + '"'
            try:
                count_row = db.execute(
                    "SELECT COUNT(*) FROM videos_fts WHERE videos_fts MATCH ?",
                    (match_q,)
                ).fetchone()
                total = count_row[0] if count_row else 0
            except Exception:
                total = 0

        if total > 0:
            rows = db.execute(
                """
                SELECT v.* FROM videos v, videos_fts fts
                WHERE v.id = fts.rowid AND videos_fts MATCH ?
                ORDER BY v.updated_at DESC
                LIMIT ? OFFSET ?
                """, (match_q, page_size, offset)
            ).fetchall()
        else:
            # FTS 不匹配（短词/特殊字符场景），用 LIKE 保底
            like = f"%{keyword}%"
            count_row = db.execute(
                "SELECT COUNT(*) FROM videos WHERE title LIKE ? OR description LIKE ? OR actors LIKE ? OR director LIKE ?",
                (like, like, like, like)
            ).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                """SELECT * FROM videos
                   WHERE title LIKE ? OR description LIKE ? OR actors LIKE ? OR director LIKE ?
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (like, like, like, like, page_size, offset)
            ).fetchall()

    return [dict(r) for r in rows], total


def get_videos_by_type(type_: str, page: int = 1, page_size: int = 30,
                       genre: str = "") -> tuple[list[dict], int]:
    """按类型分页查询；type_='recent' 时按更新时间不分类型；genre 非空时按题材过滤"""
    offset = (page - 1) * page_size
    with get_db() as db:
        if type_ == "recent":
            conds, params = [], []
            if genre:
                conds.append("genre LIKE ?")
                params.append(f"%{genre}%")
            where = (" WHERE " + " AND ".join(conds)) if conds else ""
            count_row = db.execute(f"SELECT COUNT(*) FROM videos{where}", params).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                f"SELECT * FROM videos{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset]
            ).fetchall()
        else:
            conds = ["type=?"]
            params = [type_]
            if genre:
                conds.append("genre LIKE ?")
                params.append(f"%{genre}%")
            where = " WHERE " + " AND ".join(conds)
            count_row = db.execute(f"SELECT COUNT(*) FROM videos{where}", params).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                f"SELECT * FROM videos{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset]
            ).fetchall()
    return [dict(r) for r in rows], total


def get_genres(type_: str) -> list[dict]:
    """某分类下的题材列表（含数量，按数量降序）"""
    with get_db() as db:
        rows = db.execute(
            "SELECT genre, COUNT(*) AS cnt FROM videos "
            "WHERE type=? AND genre != '' GROUP BY genre ORDER BY cnt DESC, genre ASC",
            (type_,)
        ).fetchall()
    return [{"genre": r["genre"], "count": r["cnt"]} for r in rows]


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
            """SELECT * FROM videos
               WHERE douban_score > 0 OR rating > 0
               ORDER BY COALESCE(NULLIF(douban_score, 0), NULLIF(rating, 0)) DESC, updated_at DESC
               LIMIT 40"""
        ).fetchall())
        reserved["hot"] = take(db.execute(
            "SELECT * FROM videos WHERE hits_week > 0 OR hits > 0 "
            "ORDER BY hits_week DESC, hits DESC, updated_at DESC LIMIT 40"
        ).fetchall())
        reserved["recommend"] = take(get_recommendations(40))
        reserved["recent"] = take(db.execute(
            "SELECT * FROM videos ORDER BY updated_at DESC LIMIT 40"
        ).fetchall())
        reserved["cats"] = {}
        label_map = {"movie": "电影", "tv": "电视剧", "variety": "综艺", "anime": "动漫"}
        for type_ in ["movie", "tv", "variety", "anime"]:
            reserved["cats"][type_] = take(db.execute(
                "SELECT * FROM videos WHERE type=? ORDER BY updated_at DESC LIMIT 60",
                (type_,)
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
    row = db.execute(
        """SELECT h.video_id, h.episode_id, h.progress_seconds, h.total_seconds,
                  v.title, v.cover, v.type
           FROM watch_history h
           JOIN videos v ON v.id = h.video_id
           ORDER BY h.watched_at DESC, h.id DESC
           LIMIT 1"""
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
        "SELECT * FROM videos WHERE cover != '' ORDER BY updated_at DESC LIMIT 1"
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
    """为你推荐：按最近观看记录找同类型 / 同演员 / 同导演的未看过影片"""
    with get_db() as db:
        watched = db.execute(
            """SELECT h.video_id, v.type, v.actors, v.director
               FROM watch_history h
               JOIN videos v ON v.id = h.video_id
               GROUP BY h.video_id
               ORDER BY MAX(h.watched_at) DESC, MAX(h.id) DESC
               LIMIT 8"""
        ).fetchall()
        if not watched:
            return []

        watched_ids = [r["video_id"] for r in watched]
        types = list({r["type"] for r in watched if r["type"]})
        keywords = set()
        for r in watched:
            keywords.update(_split_names(r["actors"]))
            keywords.update(_split_names(r["director"]))

        conds = [f"id NOT IN ({','.join('?' * len(watched_ids))})"]
        params = list(watched_ids)
        match_parts = []
        if types:
            match_parts.append("type IN (" + ",".join("?" * len(types)) + ")")
            params.extend(types)
        for kw in list(keywords)[:30]:
            match_parts.append("(actors LIKE ? OR director LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])
        if not match_parts:
            return []
        conds.append("(" + " OR ".join(match_parts) + ")")

        rows = db.execute(
            f"SELECT * FROM videos WHERE {' AND '.join(conds)} "
            "ORDER BY rating DESC, updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


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

        rows = db.execute(
            f"SELECT * FROM videos WHERE {' AND '.join(conds)} "
            "ORDER BY rating DESC, updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def get_watch_history(limit: int = 20) -> list[dict]:
    """获取观看历史 — 每个视频只返回最新一条记录"""
    with get_db() as db:
        rows = db.execute(
            """
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
            ORDER BY h.watched_at DESC
            LIMIT ?
            """, (limit,)
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
