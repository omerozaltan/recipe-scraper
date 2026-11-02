"""The push step: what it refuses to send, what it skips, and what it records."""

import json

import httpx
import pytest

from recipe_scraper import db, pipeline, supabase
from recipe_scraper.models import RecipeData

SITE = "food.test"
TARGET = supabase.Target(url="https://api.test", key="k")


def _scrape(n: int, title: str | None = None) -> str:
    url = f"https://food.test/{n}"
    db.upsert_pages(SITE, [(url, None)])
    db.save_recipe(SITE, url, RecipeData(
        original_name=f"The BEST Recipe {n}", ingredients_raw="flour",
        instructions_raw="1. Bake", extraction_method="jsonld"))
    if title:
        db.set_titles([(url, title)])
    return url


@pytest.fixture
def remote(monkeypatch):
    """A stand-in table. `rows` is what it already holds; `posted` is what we sent it."""
    state = {"rows": [], "posted": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["posted"].extend(json.loads(request.content))
            return httpx.Response(201)
        if request.headers.get("Prefer") == "count=exact":
            return httpx.Response(200, json=[], headers={"Content-Range": f"*/{len(state['rows'])}"})
        return httpx.Response(200, json=[{"source_url": u} for u in state["rows"]])

    monkeypatch.setattr(supabase, "from_env", lambda table=supabase.TABLE: TARGET)
    monkeypatch.setattr(supabase, "client", lambda target: httpx.Client(transport=httpx.MockTransport(handler)))
    return state


def _pushed_at(url: str):
    with db.connect() as conn:
        return conn.execute("SELECT pushed_at FROM recipes WHERE source_url = ?", (url,)).fetchone()["pushed_at"]


def test_recipes_with_uncleaned_titles_are_refused_by_default(data_dir, remote):
    """The target column is NOT NULL and the point of the titles step is the clean title,
    so sending a hype-laden one into a shared table should be a decision, not a default."""
    _scrape(1)
    with pytest.raises(RuntimeError, match="no cleaned title"):
        pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=False)
    assert remote["posted"] == []


def test_allow_untitled_sends_them_under_the_published_title(data_dir, remote):
    _scrape(1)
    pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=True)
    assert [r["title"] for r in remote["posted"]] == ["The BEST Recipe 1"]


def test_new_recipes_are_sent_and_recorded(data_dir, remote):
    urls = [_scrape(n, f"Recipe {n}") for n in range(3)]
    pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=False)
    assert {r["source_url"] for r in remote["posted"]} == set(urls)
    assert all(_pushed_at(u) for u in urls)


def test_a_source_url_the_table_already_has_is_not_sent_again(data_dir, remote):
    """Matching on source_url is what makes a push repeatable, and it keeps an import
    from overwriting a row someone has already worked from."""
    old = _scrape(1, "Recipe 1")
    new = _scrape(2, "Recipe 2")
    remote["rows"] = [old]

    pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=False)

    assert [r["source_url"] for r in remote["posted"]] == [new]
    # The one already there is recorded locally too, so the next run reports it correctly.
    assert _pushed_at(old) and _pushed_at(new)


def test_pushing_twice_sends_nothing_the_second_time(data_dir, remote):
    _scrape(1, "Recipe 1")
    pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=False)
    remote["rows"] = [r["source_url"] for r in remote["posted"]]
    remote["posted"].clear()

    pipeline.push_step(SITE, "raw_recipes", None, dry_run=False, allow_untitled=False)
    assert remote["posted"] == []


def test_a_dry_run_sends_nothing(data_dir, remote):
    _scrape(1, "Recipe 1")
    pipeline.push_step(SITE, "raw_recipes", None, dry_run=True, allow_untitled=False)
    assert remote["posted"] == []
    assert _pushed_at("https://food.test/1") is None


def test_limit_caps_what_is_sent(data_dir, remote):
    for n in range(5):
        _scrape(n, f"Recipe {n}")
    pipeline.push_step(SITE, "raw_recipes", 2, dry_run=False, allow_untitled=False)
    assert len(remote["posted"]) == 2
