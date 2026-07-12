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

            -- FTS5 全文搜索
            CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
                title, description, director, actors,
                content='videos',
                content_rowid='id',
                tokenize='unicode61'
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

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_videos_type ON videos(type);
            CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);
            CREATE INDEX IF NOT EXISTS idx_videos_updated ON videos(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_video ON episodes(video_id);
            CREATE INDEX IF NOT EXISTS idx_history_watched ON watch_history(watched_at DESC);
        """)


# ─── 查询方法 ───

def search_videos(keyword: str, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
    """搜索视频，优先 FTS5，回退 LIKE（支持中文）"""
    offset = (page - 1) * page_size
    with get_db() as db:
        # 尝试 FTS5
        try:
            count_row = db.execute(
                "SELECT COUNT(*) FROM videos_fts WHERE videos_fts MATCH ?",
                (keyword,)
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
                """, (keyword, page_size, offset)
            ).fetchall()
        else:
            # FTS 不匹配（中文场景），用 LIKE 保底
            like = f"%{keyword}%"
            count_row = db.execute(
                "SELECT COUNT(*) FROM videos WHERE title LIKE ? OR description LIKE ? OR actors LIKE ?",
                (like, like, like)
            ).fetchone()
            total = count_row[0] if count_row else 0
            rows = db.execute(
                """SELECT * FROM videos
                   WHERE title LIKE ? OR description LIKE ? OR actors LIKE ?
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (like, like, like, page_size, offset)
            ).fetchall()

    return [dict(r) for r in rows], total


def get_videos_by_type(type_: str, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
    """按类型分页查询"""
    offset = (page - 1) * page_size
    with get_db() as db:
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
    """首页数据"""
    with get_db() as db:
        sections = []
        for type_ in ["movie", "tv", "variety", "anime"]:
            rows = db.execute(
                "SELECT * FROM videos WHERE type=? ORDER BY updated_at DESC LIMIT 20",
                (type_,)
            ).fetchall()
            if rows:
                label_map = {
                    "movie": "电影", "tv": "电视剧",
                    "variety": "综艺", "anime": "动漫"
                }
                sections.append({
                    "name": label_map.get(type_, type_),
                    "type": type_,
                    "videos": [dict(r) for r in rows]
                })

        # 最近更新
        recent = db.execute(
            "SELECT * FROM videos ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        sections.insert(0, {
            "name": "最近更新",
            "type": "recent",
            "videos": [dict(r) for r in recent]
        })

    return {"sections": sections}


def get_watch_history(limit: int = 20) -> list[dict]:
    """获取观看历史"""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT h.*, v.title, v.cover, v.type, v.source,
                   e.episode_title
            FROM watch_history h
            JOIN videos v ON h.video_id = v.id
            LEFT JOIN episodes e ON h.episode_id = e.episode_num AND e.video_id = h.video_id
            ORDER BY h.watched_at DESC
            LIMIT ?
            """, (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_watch_history(video_id: int, episode_id: int | None,
                       progress: float, total: float):
    """保存/更新观看历史"""
    with get_db() as db:
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
