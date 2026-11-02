"""Pushing to the remote table: dedup, batching, and the error that will actually happen."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from recipe_scraper import supabase
from recipe_scraper.models import ExportRow

TARGET = supabase.Target(url="https://api.test", key="secret-service-role-key")


def _row(n: int) -> ExportRow:
    return ExportRow(
        title=f"Recipe {n}", original_name=f"The BEST Recipe {n}", rating=None, votes=0,
        ingredients_raw="flour", instructions_raw="1. Bake",
        source_url=f"https://food.test/{n}",
        created_at=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
    )


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_both_environment_variables_are_named_when_they_are_missing(monkeypatch):
    monkeypatch.delenv(supabase.ENV_URL, raising=False)
    monkeypatch.delenv(supabase.ENV_KEY, raising=False)
    with pytest.raises(supabase.SupabaseError) as e:
        supabase.from_env()
    assert supabase.ENV_URL in str(e.value) and supabase.ENV_KEY in str(e.value)


def test_the_key_stays_out_of_reprs_and_messages():
    """A traceback from a failed push must not be a way to leak a service_role key."""
    assert "secret-service-role-key" not in repr(TARGET)
    assert "secret-service-role-key" not in str(TARGET)


def test_row_count_is_read_from_the_content_range_header():
    def handler(request):
        assert request.headers["Prefer"] == "count=exact"
        return httpx.Response(200, json=[], headers={"Content-Range": "*/8723"})

    with _http(handler) as http:
        assert supabase.row_count(TARGET, http) == 8723


def test_existing_source_urls_reads_every_page(monkeypatch):
    monkeypatch.setattr(supabase, "READ_PAGE", 2)
    pages = [
        [{"source_url": "https://food.test/1"}, {"source_url": "https://food.test/2"}],
        [{"source_url": "https://food.test/3"}],
    ]
    seen_offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        seen_offsets.append(offset)
        return httpx.Response(200, json=pages[offset // 2] if offset // 2 < len(pages) else [])

    with _http(handler) as http:
        urls = supabase.existing_source_urls(TARGET, http)
    assert urls == {f"https://food.test/{n}" for n in (1, 2, 3)}
    assert seen_offsets == [0, 2]  # a short page ends the walk


def test_insert_sends_batches_and_reports_each_one(monkeypatch):
    monkeypatch.setattr(supabase, "WRITE_BATCH", 2)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(201)

    rows = [_row(n) for n in range(5)]
    with _http(handler) as http:
        batches = list(supabase.insert(TARGET, http, rows))

    assert [len(b) for b in batches] == [2, 2, 1]
    assert [len(b) for b in bodies] == [2, 2, 1]


def test_the_payload_leaves_the_tables_own_columns_alone():
    """`id` is the table's, and a freshly scraped recipe has been transformed into nothing."""
    payload = supabase.to_payload(_row(1))
    assert "id" not in payload and "transformed_recipe_id" not in payload
    assert set(payload) == set(supabase.SEND_COLUMNS)
    assert payload["created_at"] == "2026-11-02T12:00:00+00:00"
    assert payload["rating"] is None  # no votes means no rating, not a zero-star recipe


def test_a_refused_insert_explains_that_the_anon_key_cannot_write():
    """Row-level security rejecting the insert is the first thing anyone will hit."""
    def handler(request):
        return httpx.Response(401, json={
            "code": "42501",
            "message": 'new row violates row-level security policy for table "raw_recipes"',
        })

    with _http(handler) as http:
        with pytest.raises(supabase.SupabaseError) as e:
            list(supabase.insert(TARGET, http, [_row(1)]))
    assert "service_role" in str(e.value)


def test_a_missing_table_says_so():
    with _http(lambda r: httpx.Response(404, text="relation does not exist")) as http:
        with pytest.raises(supabase.SupabaseError) as e:
            supabase.row_count(TARGET, http)
    assert "exist" in str(e.value)
