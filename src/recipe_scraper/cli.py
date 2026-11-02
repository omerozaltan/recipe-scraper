import argparse
import json
import sys
from pathlib import Path

from . import claude_cli, config, db, doctor, export, pipeline, supabase


def show(site: str, n: int) -> None:
    for r in db.latest_recipes(site, n):
        ingredients = [line for line in r["ingredients_raw"].splitlines() if line and not line.endswith(":")]
        steps = [line for line in r["instructions_raw"].splitlines() if line[:1].isdigit()]
        rating = f"{r['rating']} ({r['votes']} votes)" if r["rating"] is not None else "no rating"
        print(f"# {r['title'] or '(untitled)'}  [original: {r['original_name']}]")
        print(f"  {rating}, {len(ingredients)} ingredients, {len(steps)} steps, via {r['extraction_method']}")
        for line in ingredients[:3]:
            print(f"  - {line}")
        print(f"  {r['source_url']}\n")


def table_name(value: str) -> str:
    try:
        return export.validate_table(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", type=Path, help=f"where state, cache and exports live (default: {config.DATA_DIR})")
    common.add_argument("--max-llm-calls", type=int, help="stop calling Claude after N calls in this run; the rest waits for the next run")

    parser = argparse.ArgumentParser(prog="recipe-scraper", description="LLM-assisted recipe scraper (Claude Code CLI).")
    parser.add_argument("--version", action="version", version=config.VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help: str, site: bool = True, limit: bool = False, filters: bool = False, table: bool = False, out: bool = False):
        p = sub.add_parser(name, help=help, parents=[common])
        if site:
            p.add_argument("site", help="site URL, e.g. https://handletheheat.com")
        if limit:
            p.add_argument("--limit", type=int, help="process at most N pages, newest first (for trial runs)")
        if filters:
            p.add_argument("--url-pattern", help="only work on URLs matching this regex, e.g. '/dessert/'")
            p.add_argument("--since", help="only work on pages whose sitemap lastmod is on/after YYYY-MM-DD")
        if table:
            p.add_argument("--table", type=table_name, default=supabase.TABLE,
                           help=f"target table name (default: {supabase.TABLE})")
        if out:
            p.add_argument("--out", type=Path, help="export directory (default: <data-dir>/export)")
        return p

    add("doctor", "check python, the claude CLI, the Supabase target and the login", site=False).add_argument(
        "--no-llm", action="store_true", help="skip the test call to Claude")
    add("run", "discover, fetch, extract, clean titles and export", limit=True, filters=True, table=True, out=True)
    add("discover", "read sitemaps and queue page URLs", filters=True)
    add("fetch", "download queued pages", limit=True, filters=True)
    add("extract", "extract recipes (schema.org data first, Claude as fallback)", limit=True, filters=True)
    add("titles", "clean recipe titles with Claude")
    add("export", "write CSV + SQL in the target table shape", table=True, out=True)
    push = add("push", "insert recipes into the Supabase table over PostgREST", limit=True, table=True)
    push.add_argument("--dry-run", action="store_true", help="report what would be sent, send nothing")
    push.add_argument("--allow-untitled", action="store_true",
                      help="send recipes whose titles have not been cleaned, under the title the site published")
    add("stats", "show progress counters and an estimate of the remaining work")
    add("show", "print the most recently extracted recipes in short form").add_argument(
        "-n", type=int, default=3, help="how many recipes (default 3)")

    args = parser.parse_args()
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    claude_cli.set_budget(args.max_llm_calls)

    if args.command == "doctor":
        sys.exit(0 if doctor.run(check_llm=not args.no_llm) else 1)

    # `run` stops before `push`: sending recipes to a shared table is a separate decision.
    filters = (getattr(args, "url_pattern", None), getattr(args, "since", None))
    if args.command in ("run", "discover"):
        try:
            pipeline.discover_step(args.site, *filters)
        except RuntimeError as e:
            sys.exit(f"[discover] {e}")
    if args.command in ("run", "fetch"):
        pipeline.fetch_step(args.site, args.limit, *filters)
    if args.command in ("run", "extract"):
        pipeline.extract_step(args.site, args.limit, *filters)
    if args.command in ("run", "titles"):
        pipeline.titles_step(args.site)
    if args.command in ("run", "export"):
        pipeline.export_step(args.site, args.table, args.out)
    if args.command == "push":
        try:
            pipeline.push_step(args.site, args.table, args.limit, args.dry_run, args.allow_untitled)
        except (RuntimeError, supabase.SupabaseError) as e:
            sys.exit(f"[push] {e}")
    if args.command == "show":
        show(pipeline.site_key(args.site), args.n)
    if args.command in ("run", "stats"):
        print(json.dumps({**db.stats(pipeline.site_key(args.site)), "remaining": pipeline.estimate(args.site)}, indent=2))
