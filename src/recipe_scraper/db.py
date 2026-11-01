import sqlite3
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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
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


def pages_by_status(site: str, status: str, limit: int | None = None) -> list[sqlite3.Row]:
    # Newest first, so a --limit trial samples current content rather than a blog's oldest posts.
    sql = "SELECT url, html_path FROM pages WHERE site = ? AND status = ? ORDER BY lastmod DESC NULLS LAST, rowid"
    params: tuple = (site, status)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def set_page(url: str, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE pages SET {cols} WHERE url = ?", (*fields.values(), url))


def save_recipe(site: str, url: str, rec: RecipeData) -> None:
    # Keep the cleaned title and first-seen timestamp unless the source name changed.
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (source_url, site, original_name, rating, votes, ingredients_raw,
                                 instructions_raw, extraction_method, raw_jsonld, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_url) DO UPDATE SET
                title = CASE WHEN recipes.original_name = excluded.original_name THEN recipes.title END,
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


def recipes_for_export(site: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM recipes WHERE site = ? ORDER BY rowid", (site,)).fetchall()


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
            "llm_all_sites": dict(conn.execute(
                "SELECT 'calls', COUNT(*) FROM llm_calls UNION ALL SELECT 'failed', COUNT(*) FROM llm_calls WHERE ok = 0 "
                "UNION ALL SELECT 'cost_usd', ROUND(COALESCE(SUM(cost_usd), 0), 4) FROM llm_calls").fetchall()),
        }
