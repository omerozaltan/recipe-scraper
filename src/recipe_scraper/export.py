"""Export recipes in the shape of the Supabase `recipes` table (CSV + upsert SQL)."""

import csv
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import db
from .models import ExportRow

COLUMNS = list(ExportRow.model_fields)


def export(site: str, out_dir: Path, table: str = "recipes") -> tuple[Path, Path, int, list[str]]:
    rows, problems = [], []
    for r in db.recipes_for_export(site):
        try:
            rows.append(ExportRow(
                title=r["title"] or "",
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

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{site.replace('.', '_')}_{datetime.now():%Y%m%d_%H%M%S}"
    csv_path = out_dir / f"{stem}.csv"
    sql_path = out_dir / f"{stem}.sql"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            data = row.model_dump()
            data["created_at"] = row.created_at.isoformat()
            writer.writerow({k: "" if v is None else v for k, v in data.items()})

    sql_path.write_text(_to_sql(rows, table), "utf-8")
    return csv_path, sql_path, len(rows), problems


def _to_sql(rows: list[ExportRow], table: str) -> str:
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c not in ("source_url", "created_at", "transformed_recipe_id"))
    lines = [
        "-- Upsert keyed on source_url, so re-running an export never duplicates recipes.",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_source_url_key ON {table} (source_url);",
        "",
        "BEGIN;",
    ]
    for row in rows:
        data = row.model_dump()
        data["created_at"] = row.created_at.isoformat()
        values = ", ".join(_literal(data[c]) for c in COLUMNS)
        lines.append(
            f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES ({values})\n"
            f"  ON CONFLICT (source_url) DO UPDATE SET {updates};"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"
