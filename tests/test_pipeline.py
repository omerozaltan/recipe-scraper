"""Step behaviour: one bad page must not stop a run, and filters must apply to the queue."""

from recipe_scraper import db, pipeline
from recipe_scraper.fetch import html_path

from .conftest import RECIPE_HTML

SITE = "w.com"


def _queue_fetched(url: str, html: str | None = RECIPE_HTML) -> str:
    db.upsert_pages(SITE, [(url, None)])
    path = html_path(url)
    if html is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, "utf-8")
    db.set_page(url, status="fetched", html_path=path.name)
    return url


def _status(url: str) -> str:
    with db.connect() as conn:
        return conn.execute("SELECT status FROM pages WHERE url = ?", (url,)).fetchone()["status"]


def test_a_page_that_cannot_be_read_is_marked_failed_and_the_rest_continue(data_dir):
    """The missing-cache-file case used to raise out of the step and wedge the queue:
    the same page sorts first on every later run, so it would abort every run after it."""
    gone = _queue_fetched("https://w.com/gone", html=None)
    good = _queue_fetched("https://w.com/good")

    pipeline.extract_step(SITE, None)  # must not raise

    assert _status(gone) == "failed"
    assert _status(good) == "extracted"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1


def test_the_failed_page_carries_its_reason(data_dir):
    url = _queue_fetched("https://w.com/gone", html=None)
    pipeline.extract_step(SITE, None)
    with db.connect() as conn:
        assert "FileNotFoundError" in conn.execute(
            "SELECT error FROM pages WHERE url = ?", (url,)).fetchone()["error"]


def test_url_pattern_narrows_a_queue_an_earlier_wider_discover_filled(data_dir):
    """`discover` used to be the only place the filter applied, so narrowing a run on a
    site whose sitemap had already been read fully did nothing at all."""
    wanted = [_queue_fetched(f"https://w.com/desserts/{i}") for i in range(3)]
    other = [_queue_fetched(f"https://w.com/pasta/{i}") for i in range(2)]

    pipeline.extract_step(SITE, None, url_pattern="/desserts/")

    assert [_status(u) for u in wanted] == ["extracted"] * 3
    assert [_status(u) for u in other] == ["fetched"] * 2


def test_since_filters_the_queue_on_sitemap_lastmod(data_dir):
    db.upsert_pages(SITE, [
        ("https://w.com/old", "2024-01-01"),
        ("https://w.com/new", "2026-06-01"),
        ("https://w.com/undated", None),
    ])
    urls = {r["url"] for r in db.pages_by_status(SITE, "discovered", since="2026-01-01")}
    # Undated pages are kept on purpose: their age is unknown, not old.
    assert urls == {"https://w.com/new", "https://w.com/undated"}


def test_limit_applies_after_the_filters(data_dir):
    for i in range(4):
        _queue_fetched(f"https://w.com/desserts/{i}")
    _queue_fetched("https://w.com/pasta/0")
    rows = db.pages_by_status(SITE, "fetched", limit=2, url_pattern="/desserts/")
    assert len(rows) == 2
    assert all("/desserts/" in r["url"] for r in rows)
