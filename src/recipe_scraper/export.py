"""Export recipes in the shape of the target `raw_recipes` table (CSV + insert SQL)."""

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from . import db
from .models import ExportRow

COLUMNS = list(ExportRow.model_fields)

# Postgres types for the generated SQL. A VALUES column that is NULL in every row would
# otherwise arrive as text and be rejected by a real / integer / timestamptz column.
CASTS = {
    "rating": "real",
    "votes": "integer",
    "created_at": "timestamptz",
}
ROWS_PER_STATEMENT = 500

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExportResult(NamedTuple):
    csv_path: Path
    sql_path: Path
    count: int
    problems: list[str]
    untitled: int  # exported with original_name as the title because `titles` has not run


def validate_table(table: str) -> str:
    """Table names end up in generated SQL, so only accept a plain identifier."""
    if not IDENTIFIER.match(table):
        raise ValueError(f"not a valid table name: {table!r} (letters, digits and underscore only)")
    return table


def to_rows(site: str, unpushed_only: bool = False) -> tuple[list[ExportRow], list[str], int]:
    """Validated rows, the source URLs that could not be validated, and the untitled count."""
    rows, problems, untitled = [], [], 0
    for r in db.recipes_for_export(site, unpushed_only):
        # A recipe whose title has not been cleaned yet still has the title the site published.
        # Falling back to it keeps the recipe in the output; dropping it loses a scraped recipe
        # for a reason the user can fix by re-running `titles`.
        title = r["title"]
        if not title:
            untitled += 1
            title = r["original_name"]
        try:
            rows.append(ExportRow(
                title=title or "",
                original_name=r["original_name"],
                rating=r["rating"],
                votes=r["votes"],
                ingredients_raw=r["ingredients_raw"],
                instructions_raw=r["instructions_raw"],
                source_url=r["source_url"],
                created_at=datetime.fromisoformat(r["created_at"]),
            ))
        except ValidationError as e:
            fields = ", ".join(str(err["loc"][0]) for err in e.errors())
            problems.append(f"{r['source_url']}: invalid {fields}")
    return rows, problems, untitled


def export(site: str, out_dir: Path, table: str = "raw_recipes") -> ExportResult:
    validate_table(table)
    rows, problems, untitled = to_rows(site)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{site.replace('.', '_')}_{datetime.now():%Y%m%d_%H%M%S}"
    csv_path = out_dir / f"{stem}.csv"
    sql_path = out_dir / f"{stem}.sql"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if v is None else v for k, v in _as_data(row).items()})

    sql_path.write_text(_to_sql(rows, table), "utf-8")
    return ExportResult(csv_path, sql_path, len(rows), problems, untitled)


def _as_data(row: ExportRow) -> dict:
    data = row.model_dump()
    data["created_at"] = row.created_at.isoformat()
    return data


def _to_sql(rows: list[ExportRow], table: str) -> str:
    """Insert rows that are not in the table yet, keyed on source_url.

    Deliberately not an upsert: the target table holds scraped rows as they arrived and an
    operator may already have worked from one, so an import never rewrites an existing row.
    Needs no unique index on source_url, so importing changes no schema.
    """
    cols = ", ".join(COLUMNS)
    select = ", ".join(f"v.{c}::{CASTS[c]}" if c in CASTS else f"v.{c}" for c in COLUMNS)
    lines = [
        f"-- Inserts only source_urls that {table} does not have yet, so re-running is safe",
        f"-- and never overwrites a row someone has already worked from.",
        "BEGIN;",
    ]
    for start in range(0, len(rows), ROWS_PER_STATEMENT):
        values = ",\n    ".join(
            "(" + ", ".join(_literal(_as_data(row)[c]) for c in COLUMNS) + ")"
            for row in rows[start:start + ROWS_PER_STATEMENT]
        )
        lines.append(
            f"INSERT INTO {table} ({cols})\n"
            f"SELECT {select}\n"
            f"FROM (VALUES\n    {values}\n) AS v ({cols})\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.source_url = v.source_url);"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"
