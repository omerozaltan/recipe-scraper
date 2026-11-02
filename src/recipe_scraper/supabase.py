"""Push scraped recipes into a Supabase table over PostgREST.

The target table holds recipes as they were scraped, for someone to triage later. Rows are
matched on `source_url`: a source URL the table already has is never sent again and never
overwritten, so a push can be repeated and can resume after an interruption.

Writing needs a key that passes the table's row-level security. On a stock Supabase the
`anon` key is granted SELECT but not INSERT, so a push with it fails with PostgREST error
42501; the `service_role` key bypasses RLS and is what this module expects. The key is read
from the environment, never logged, and never written to the local database.
"""

import os
from dataclasses import dataclass, field
from typing import Iterable, Iterator

import httpx

from .models import ExportRow

TABLE = "raw_recipes"
READ_PAGE = 1000  # source_urls per read request
WRITE_BATCH = 200  # rows per insert request
TIMEOUT = 60

# Columns we send. `id` and `transformed_recipe_id` are left to the table's own defaults:
# a freshly scraped row has not been transformed into anything yet.
SEND_COLUMNS = ["title", "original_name", "rating", "votes",
                "ingredients_raw", "instructions_raw", "source_url", "created_at"]

ENV_URL = "SUPABASE_URL"
ENV_KEY = "SUPABASE_SERVICE_ROLE_KEY"


class SupabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    url: str
    key: str = field(repr=False)  # a service_role key; keep it out of tracebacks
    table: str = TABLE

    @property
    def endpoint(self) -> str:
        return f"{self.url.rstrip('/')}/rest/v1/{self.table}"

    def __str__(self) -> str:
        return f"{self.url.rstrip('/')} table {self.table}"


def from_env(table: str = TABLE) -> Target:
    url, key = os.environ.get(ENV_URL), os.environ.get(ENV_KEY)
    missing = [name for name, value in ((ENV_URL, url), (ENV_KEY, key)) if not value]
    if missing:
        raise SupabaseError(
            f"{' and '.join(missing)} not set. {ENV_URL} is the project URL "
            f"(e.g. https://xxxx.supabase.co) and {ENV_KEY} is the service_role key: "
            "the anon key cannot insert when row-level security is on."
        )
    assert url and key
    return Target(url=url, key=key, table=table)


def client(target: Target) -> httpx.Client:
    return httpx.Client(
        headers={
            "apikey": target.key,
            "Authorization": f"Bearer {target.key}",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    )


def row_count(target: Target, http: httpx.Client) -> int:
    """Total rows in the table, from the Content-Range header."""
    resp = http.get(target.endpoint, params={"select": "source_url", "limit": 0},
                    headers={"Prefer": "count=exact"})
    _raise_for_status(resp, "count rows in")
    return int(resp.headers.get("content-range", "*/0").split("/")[-1] or 0)


def existing_source_urls(target: Target, http: httpx.Client) -> set[str]:
    """Every source_url already in the table.

    Read whole rather than asked per URL: PostgREST's `in.()` filter needs each value
    quoted and escaped, and recipe URLs carry commas and parentheses often enough that
    building that filter is the more fragile of the two.
    """
    seen: set[str] = set()
    offset = 0
    while True:
        resp = http.get(target.endpoint, params={
            "select": "source_url",
            "source_url": "not.is.null",
            "order": "source_url",
            "offset": offset,
            "limit": READ_PAGE,
        })
        _raise_for_status(resp, "read source_urls from")
        page = resp.json()
        seen.update(row["source_url"] for row in page if row.get("source_url"))
        if len(page) < READ_PAGE:
            return seen
        offset += len(page)


def to_payload(row: ExportRow) -> dict:
    data = row.model_dump()
    data["created_at"] = row.created_at.isoformat()
    return {c: data[c] for c in SEND_COLUMNS}


def insert(target: Target, http: httpx.Client, rows: Iterable[ExportRow]) -> Iterator[list[ExportRow]]:
    """Insert in batches, yielding each batch after the server accepted it.

    Yielding per batch rather than returning a total lets the caller record progress as it
    happens, so an interrupted push does not re-send what already landed.
    """
    for batch in _batched(list(rows), WRITE_BATCH):
        resp = http.post(target.endpoint, json=[to_payload(r) for r in batch],
                         headers={"Prefer": "return=minimal"})
        _raise_for_status(resp, "insert into")
        yield batch


def _batched(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.is_success:
        return
    body = resp.text[:300].strip()
    hint = ""
    if resp.status_code in (401, 403) or "42501" in body:
        hint = (f" -- row-level security refused this. {ENV_KEY} has to be the service_role key; "
                "the anon key can usually read but not insert.")
    elif resp.status_code == 404:
        hint = " -- does the table exist, and is it exposed by the API?"
    raise SupabaseError(f"could not {action} the table (HTTP {resp.status_code}): {body}{hint}")
