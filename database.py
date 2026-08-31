"""
Слой работы с базой данных (SQLite через aiosqlite).

Таблицы:
    tracked_repos — какие репозитории бот отслеживает и что видел последним
    posts         — черновики и опубликованные посты
    metrics       — метрики вовлечённости для каждого поста

Для перехода на PostgreSQL достаточно заменить реализацию функций в этом
файле (например, на SQLAlchemy/asyncpg) — остальной код работает с ним
через эти функции и схему таблиц не знает.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import aiosqlite

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_repos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name         TEXT UNIQUE NOT NULL,
    url               TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT 'manual',   -- manual | trending | webhook
    last_commit_sha   TEXT,
    last_release_tag  TEXT,
    added_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name      TEXT NOT NULL,
    repo_url            TEXT NOT NULL,
    dedup_key           TEXT NOT NULL,   -- repo_full_name + ':' + commit/release id
    tags                TEXT NOT NULL DEFAULT '',
    draft_text          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',  -- draft | published | rejected
    admin_message_id    INTEGER,
    channel_message_id  INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    published_at        TEXT,
    UNIQUE(dedup_key)
);

CREATE TABLE IF NOT EXISTS metrics (
    post_id         INTEGER PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    github_clicks   INTEGER NOT NULL DEFAULT 0,
    discuss_clicks  INTEGER NOT NULL DEFAULT 0,
    comments_count  INTEGER NOT NULL DEFAULT 0,
    reactions_count INTEGER NOT NULL DEFAULT 0,
    reposts_count   INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ---------------------------- tracked_repos ----------------------------

async def get_tracked_repos() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tracked_repos")
        return list(await cur.fetchall())


async def upsert_tracked_repo(full_name: str, url: str, source: str = "manual") -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO tracked_repos (full_name, url, source)
               VALUES (?, ?, ?)
               ON CONFLICT(full_name) DO NOTHING""",
            (full_name, url, source),
        )
        await db.commit()


async def update_repo_pointer(
    full_name: str, *, commit_sha: str | None = None, release_tag: str | None = None
) -> None:
    fields, values = [], []
    if commit_sha is not None:
        fields.append("last_commit_sha = ?")
        values.append(commit_sha)
    if release_tag is not None:
        fields.append("last_release_tag = ?")
        values.append(release_tag)
    if not fields:
        return
    values.append(full_name)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE tracked_repos SET {', '.join(fields)} WHERE full_name = ?", values
        )
        await db.commit()


# -------------------------------- posts --------------------------------

async def is_duplicate(dedup_key: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT 1 FROM posts WHERE dedup_key = ?", (dedup_key,))
        return await cur.fetchone() is not None


async def create_draft_post(
    *, repo_full_name: str, repo_url: str, dedup_key: str, tags: list[str], draft_text: str
) -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            """INSERT INTO posts (repo_full_name, repo_url, dedup_key, tags, draft_text)
               VALUES (?, ?, ?, ?, ?)""",
            (repo_full_name, repo_url, dedup_key, ",".join(tags), draft_text),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def get_post(post_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
        return await cur.fetchone()


async def get_post_by_channel_message(channel_message_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM posts WHERE channel_message_id = ?", (channel_message_id,)
        )
        return await cur.fetchone()


async def update_draft_text(post_id: int, new_text: str, tags: list[str] | None = None) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        if tags is not None:
            await db.execute(
                "UPDATE posts SET draft_text = ?, tags = ? WHERE id = ?",
                (new_text, ",".join(tags), post_id),
            )
        else:
            await db.execute("UPDATE posts SET draft_text = ? WHERE id = ?", (new_text, post_id))
        await db.commit()


async def set_admin_message_id(post_id: int, message_id: int) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE posts SET admin_message_id = ? WHERE id = ?", (message_id, post_id)
        )
        await db.commit()


async def publish_post(post_id: int, channel_message_id: int) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """UPDATE posts SET status = 'published', channel_message_id = ?,
               published_at = datetime('now') WHERE id = ?""",
            (channel_message_id, post_id),
        )
        await db.execute("INSERT OR IGNORE INTO metrics (post_id) VALUES (?)", (post_id,))
        await db.commit()


async def reject_post(post_id: int) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE posts SET status = 'rejected' WHERE id = ?", (post_id,))
        await db.commit()


# ------------------------------- metrics -------------------------------

async def bump_metric(post_id: int, field: str, amount: int = 1) -> None:
    assert field in {
        "github_clicks",
        "discuss_clicks",
        "comments_count",
        "reactions_count",
        "reposts_count",
    }
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO metrics (post_id) VALUES (?)", (post_id,))
        await db.execute(
            f"""UPDATE metrics SET {field} = {field} + ?, updated_at = datetime('now')
                WHERE post_id = ?""",
            (amount, post_id),
        )
        await db.commit()


async def set_metric_counts(
    post_id: int,
    *,
    comments: int | None = None,
    reactions: int | None = None,
    reposts: int | None = None,
) -> None:
    fields, values = [], []
    if comments is not None:
        fields.append("comments_count = ?")
        values.append(comments)
    if reactions is not None:
        fields.append("reactions_count = ?")
        values.append(reactions)
    if reposts is not None:
        fields.append("reposts_count = ?")
        values.append(reposts)
    if not fields:
        return
    fields.append("updated_at = datetime('now')")
    values.append(post_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO metrics (post_id) VALUES (?)", (post_id,))
        await db.execute(f"UPDATE metrics SET {', '.join(fields)} WHERE post_id = ?", values)
        await db.commit()


async def get_posts_with_metrics(since: dt.datetime | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT p.id, p.repo_full_name, p.repo_url, p.tags, p.published_at,
               COALESCE(m.github_clicks, 0)   AS github_clicks,
               COALESCE(m.discuss_clicks, 0)  AS discuss_clicks,
               COALESCE(m.comments_count, 0)  AS comments_count,
               COALESCE(m.reactions_count, 0) AS reactions_count,
               COALESCE(m.reposts_count, 0)   AS reposts_count
        FROM posts p
        LEFT JOIN metrics m ON m.post_id = p.id
        WHERE p.status = 'published'
    """
    params: list[Any] = []
    if since is not None:
        query += " AND p.published_at >= ?"
        params.append(since.strftime("%Y-%m-%d %H:%M:%S"))
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
