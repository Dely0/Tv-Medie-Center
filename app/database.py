"""SQLite 数据库模块"""
import sqlite3
import json
import os
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
        _init_fts(db)


# trigram 分词器支持中文子串匹配（SQLite >= 3.34），否则回退 unicode61 + LIKE
FTS_TOKENIZER = "trigram" if sqlite3.sqlite_version_info >= (3, 34, 0) else "unicode61"


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


def get_videos_by_type(type_: str, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
    """按类型分页查询；type_='recent' 时按更新时间不分类型"""
    offset = (page - 1) * page_size
    with get_db() as db:
        if type_ == "recent":
            count_row = db.execute("SELECT COUNT(*) FROM videos").fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                "SELECT * FROM videos ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (page_size, offset)
            ).fetchall()
        else:
            count_row = db.execute("SELECT COUNT(*) FROM videos WHERE type=?", (type_,)).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                "SELECT * FROM videos WHERE type=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (type_, page_size, offset)
            ).fetchall()
    return [dict(r) for r in rows], total


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
        sections = []

        # 最近更新（一屏内 7 个）
        recent = db.execute(
            "SELECT * FROM videos ORDER BY updated_at DESC LIMIT 7"
        ).fetchall()
        recent_videos = []
        for r in recent:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            recent_videos.append(dict(r))
        sections.append({"name": "最近更新", "type": "recent", "videos": recent_videos})

        # 分类栏目：跳过已在前面出现过的影片，每栏最多 7 个
        label_map = {"movie": "电影", "tv": "电视剧", "variety": "综艺", "anime": "动漫"}
        for type_ in ["movie", "tv", "variety", "anime"]:
            rows = db.execute(
                "SELECT * FROM videos WHERE type=? ORDER BY updated_at DESC LIMIT 35",
                (type_,)
            ).fetchall()
            items = []
            for r in rows:
                if len(items) >= 7:
                    break
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                items.append(dict(r))
            if items:
                sections.append({"name": label_map[type_], "type": type_, "videos": items})

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
                      "area", "director", "actors", "rating", "source"]
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
                   area, director, actors, rating, source, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video["title"], video.get("type", "movie"),
                 video.get("cover"), video.get("description"),
                 video.get("year"), video.get("area"),
                 video.get("director"), video.get("actors"),
                 video.get("rating"), video.get("source", ""),
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
