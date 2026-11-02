"""Export: what reaches the CSV and the SQL, and what the SQL is allowed to do."""

import pytest

from recipe_scraper import db, export
from recipe_scraper.models import RecipeData


def _recipe(**over) -> RecipeData:
    return RecipeData(**{
        "original_name": "Easy Brownies",
        "ingredients_raw": "cocoa",
        "instructions_raw": "1. Bake",
        "extraction_method": "jsonld",
        **over,
    })


def _save(site="y.com", url="https://y.com/b", **over):
    db.upsert_pages(site, [(url, None)])
    db.save_recipe(site, url, _recipe(**over))
    return url


def test_recipe_with_no_cleaned_title_is_exported_not_dropped(data_dir):
    """`titles` may not have run: an uncleaned recipe is still a scraped recipe."""
    _save()
    result = export.export("y.com", data_dir / "out")
    assert result.count == 1
    assert result.untitled == 1
    assert result.problems == []
    assert "Easy Brownies" in result.csv_path.read_text()


def test_cleaned_title_wins_and_is_not_counted_as_untitled(data_dir):
    url = _save()
    db.set_titles([(url, "Brownies")])
    result = export.export("y.com", data_dir / "out")
    assert (result.count, result.untitled) == (1, 0)
    assert "Brownies" in result.csv_path.read_text()


@pytest.mark.parametrize("bad", [
    "recipes; DROP TABLE users; --",
    "raw recipes",
    "public.raw_recipes",
    "",
    "1table",
])
def test_table_name_must_be_a_plain_identifier(bad):
    with pytest.raises(ValueError):
        export.validate_table(bad)


@pytest.mark.parametrize("good", ["raw_recipes", "recipes", "_t", "T9"])
def test_ordinary_table_names_are_accepted(good):
    assert export.validate_table(good) == good


def test_export_refuses_an_unsafe_table_name(data_dir):
    _save()
    with pytest.raises(ValueError):
        export.export("y.com", data_dir / "out", "x; DROP TABLE y; --")


def test_sql_inserts_what_is_missing_and_never_overwrites(data_dir):
    _save()
    sql = export.export("y.com", data_dir / "out", "raw_recipes").sql_path.read_text()
    assert "WHERE NOT EXISTS" in sql
    # An import must not change the target's schema, nor rewrite a row someone already used.
    assert "CREATE UNIQUE INDEX" not in sql
    assert "ON CONFLICT" not in sql
    assert "DO UPDATE" not in sql


def test_sql_casts_columns_that_can_be_null_in_every_row(data_dir):
    _save(rating=None)
    sql = export.export("y.com", data_dir / "out").sql_path.read_text()
    # Without the casts a VALUES column of only NULLs arrives as text and Postgres
    # rejects it for a real / integer / timestamptz column.
    for cast in ("v.rating::real", "v.votes::integer", "v.created_at::timestamptz"):
        assert cast in sql


def test_sql_escapes_quotes_in_recipe_text(data_dir):
    _save(original_name="O'Brien's \"best\" pie")
    sql = export.export("y.com", data_dir / "out").sql_path.read_text()
    assert "O''Brien''s" in sql


def test_rows_split_into_several_statements(data_dir, monkeypatch):
    monkeypatch.setattr(export, "ROWS_PER_STATEMENT", 2)
    for i in range(5):
        _save(url=f"https://y.com/{i}")
    sql = export.export("y.com", data_dir / "out").sql_path.read_text()
    assert sql.count("INSERT INTO") == 3  # 2 + 2 + 1
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1
