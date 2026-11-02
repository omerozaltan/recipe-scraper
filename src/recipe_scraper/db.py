import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .models import RecipeData

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url         TEXT PRIMARY KEY,
    site        TEXT NOT NULL,
    lastmod     TEXT,
    status      TEXT NOT NULL DEFAULT 'discovered',  -- discovered | fetched | extracted | skipped | failed
    page_type   TEXT,                                -- recipe | roundup | article | other
    html_path   TEXT,                                -- file name inside the data dir's html/ folder
    error       TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_site_status ON pages (site, status);

CREATE TABLE IF NOT EXISTS recipes (
    source_url         TEXT PRIMARY KEY REFERENCES pages (url),
    site               TEXT NOT NULL,
    original_name      TEXT NOT NULL,
    title              TEXT,
    rating             REAL,
    votes              INTEGER NOT NULL DEFAULT 0,
    ingredients_raw    TEXT NOT NULL,
    instructions_raw   TEXT NOT NULL,
    extraction_method  TEXT NOT NULL,                -- jsonld | microdata | llm
    raw_jsonld         TEXT,
    pushed_at          TEXT,                         -- when this row reached the remote table
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id           INTEGER PRIMARY KEY,
    task         TEXT NOT NULL,
    model        TEXT NOT NULL,
    ok           INTEGER NOT NULL,
    duration_ms  INTEGER,
    cost_usd     REAL,
    error        TEXT,
    created_at   TEXT NOT NULL
);
"""

# Columns added after 0.2.0; existing databases get them on the next connect.
MIGRATIONS = [("recipes", "pushed_at", "TEXT")]

_init_lock = threading.Lock()
_initialised: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _initialise(conn: sqlite3.Connection, key: str) -> None:
    """Create the schema and set the pragmas once per data dir, not on every connection."""
    with _init_lock:
        if key in _initialised:
            return
        conn.executescript(SCHEMA)
        # WAL lets the LLM worker threads write while another connection reads.
        conn.execute("PRAGMA journal_mode = WAL")
        for table, column, decl in MIGRATIONS:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
        _initialised.add(key)


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _initialise(conn, str(config.DB_PATH))
        conn.execute("PRAGMA synchronous = NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_pages(site: str, urls: list[tuple[str, str | None]]) -> tuple[int, int]:
    """Insert newly discovered URLs; re-queue pages whose sitemap lastmod changed."""
    added = changed = 0
    with connect() as conn:
        for url, lastmod in urls:
            row = conn.execute("SELECT lastmod FROM pages WHERE url = ?", (url,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO pages (url, site, lastmod, updated_at) VALUES (?, ?, ?, ?)",
                    (url, site, lastmod, now()),
                )
                added += 1
            elif lastmod and row["lastmod"] != lastmod:
                conn.execute(
                    "UPDATE pages SET lastmod = ?, status = 'discovered', error = NULL, updated_at = ? WHERE url = ?",
                    (lastmod, now(), url),
                )
                changed += 1
    return added, changed


def pages_by_status(site: str, status: str, limit: int | None = None,
                    url_pattern: str | None = None, since: str | None = None) -> list[sqlite3.Row]:
    """Queued pages for one step.

    `url_pattern` and `since` are the same filters `discover` accepts. They are applied here
    too, so narrowing a run also narrows what is fetched and extracted from a queue that an
    earlier, wider `discover` already filled.
    """
    # Newest first, so a --limit trial samples current content rather than a blog's oldest posts.
    sql = "SELECT url, html_path FROM pages WHERE site = ? AND status = ?"
    params: tuple = (site, status)
    if since:
        # Pages without lastmod are kept: we can't tell their age.
        sql += " AND (lastmod IS NULL OR substr(lastmod, 1, 10) >= ?)"
        params += (since,)
    sql += " ORDER BY lastmod DESC NULLS LAST, rowid"
    rows = _filter_by_pattern(sql, params, url_pattern)
    return rows[:limit] if limit else rows


def _filter_by_pattern(sql: str, params: tuple, url_pattern: str | None) -> list[sqlite3.Row]:
    # Python's `re` rather than SQLite's LIKE: --url-pattern is documented as a regex.
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    if not url_pattern:
        return rows
    regex = re.compile(url_pattern)
    return [r for r in rows if regex.search(r["url"])]


def set_page(url: str, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE pages SET {cols} WHERE url = ?", (*fields.values(), url))


def save_recipe(site: str, url: str, rec: RecipeData) -> None:
    # Keep the cleaned title and first-seen timestamp unless the source name changed.
    # A changed recipe has to be pushed again, so pushed_at is cleared with the title.
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (source_url, site, original_name, rating, votes, ingredients_raw,
                                 instructions_raw, extraction_method, raw_jsonld, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_url) DO UPDATE SET
                title = CASE WHEN recipes.original_name = excluded.original_name THEN recipes.title END,
                pushed_at = CASE WHEN recipes.original_name = excluded.original_name THEN recipes.pushed_at END,
                original_name = excluded.original_name,
                rating = excluded.rating,
                votes = excluded.votes,
                ingredients_raw = excluded.ingredients_raw,
                instructions_raw = excluded.instructions_raw,
                extraction_method = excluded.extraction_method,
                raw_jsonld = excluded.raw_jsonld
            """,
            (url, site, rec.original_name, rec.rating, rec.votes, rec.ingredients_raw,
             rec.instructions_raw, rec.extraction_method, rec.raw_jsonld, now()),
        )


def recipes_without_title(site: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT source_url, original_name FROM recipes WHERE site = ? AND title IS NULL ORDER BY rowid",
            (site,),
        ).fetchall()


def set_titles(pairs: list[tuple[str, str]]) -> None:
    with connect() as conn:
        conn.executemany("UPDATE recipes SET title = ? WHERE source_url = ?", [(t, u) for u, t in pairs])


def latest_recipes(site: str, n: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM recipes WHERE site = ? ORDER BY rowid DESC LIMIT ?", (site, n)).fetchall()


def recipes_for_export(site: str, unpushed_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM recipes WHERE site = ?"
    if unpushed_only:
        sql += " AND pushed_at IS NULL"
    with connect() as conn:
        return conn.execute(sql + " ORDER BY rowid", (site,)).fetchall()


def mark_pushed(source_urls: list[str]) -> None:
    stamp = now()
    with connect() as conn:
        conn.executemany("UPDATE recipes SET pushed_at = ? WHERE source_url = ?",
                         [(stamp, u) for u in source_urls])


def log_llm_call(task: str, model: str, ok: bool, duration_ms: int | None = None,
                 cost_usd: float | None = None, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_calls (task, model, ok, duration_ms, cost_usd, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task, model, int(ok), duration_ms, cost_usd, error, now()),
        )


def stats(site: str) -> dict:
    with connect() as conn:
        return {
            "pages": dict(conn.execute(
                "SELECT status, COUNT(*) FROM pages WHERE site = ? GROUP BY status", (site,)).fetchall()),
            "page_types": dict(conn.execute(
                "SELECT COALESCE(page_type, '?'), COUNT(*) FROM pages WHERE site = ? AND page_type IS NOT NULL GROUP BY page_type",
                (site,)).fetchall()),
            "recipes": dict(conn.execute(
                "SELECT extraction_method, COUNT(*) FROM recipes WHERE site = ? GROUP BY extraction_method", (site,)).fetchall()),
            "untitled": conn.execute(
                "SELECT COUNT(*) FROM recipes WHERE site = ? AND title IS NULL", (site,)).fetchone()[0],
            "pushed": conn.execute(
                "SELECT COUNT(*) FROM recipes WHERE site = ? AND pushed_at IS NOT NULL", (site,)).fetchone()[0],
            "llm_all_sites": dict(conn.execute(
                "SELECT 'calls', COUNT(*) FROM llm_calls UNION ALL SELECT 'failed', COUNT(*) FROM llm_calls WHERE ok = 0 "
                "UNION ALL SELECT 'cost_usd', ROUND(COALESCE(SUM(cost_usd), 0), 4) FROM llm_calls").fetchall()),
        }
