---
name: scrape
description: Scrape all recipes from a food or recipe website (for example "scrape the recipes from handletheheat.com", "bu siteden tarifleri çek") into CSV, SQL, or a Supabase table. Use when the user wants recipes collected from a site, asks how far a scrape has got, wants scraped recipes exported, or wants them loaded into Supabase.
argument-hint: <site-url> [what you want]
allowed-tools: Bash(recipe-scraper *), PowerShell(recipe-scraper *)
---

# Recipe scraper

`recipe-scraper` is on PATH while this plugin is enabled. It keeps per-site state in a local
SQLite database, so every command resumes where the previous one stopped and can be re-run safely.

Request: $ARGUMENTS

## How it works (so you can explain it)

1. **discover**: reads robots.txt and the sitemaps, queues page URLs. URLs that robots.txt disallows are dropped.
2. **fetch**: downloads pages at about one request per second (or the site's Crawl-delay).
3. **extract**: schema.org Recipe data (JSON-LD or microdata) is used when present, with no LLM involved.
   Other pages go to Claude: a fast model classifies them (recipe, roundup, article, other) and a
   stronger model extracts recipes that have no structured data.
4. **titles**: Claude cleans titles in batches ("The BEST Chewy Cookies (Video)" becomes "Chewy Cookies").
5. **export**: CSV and SQL with the columns `title, original_name, rating, votes, ingredients_raw,
   instructions_raw, source_url, transformed_recipe_id, created_at`.
6. **push** (optional, and only when the user asks): inserts the same rows into a Supabase table.

Claude calls run as separate `claude -p` processes on the user's own Claude Code login, so they count
toward the user's usage.

## Workflow

1. **Health check**, once per session before the first scrape: `recipe-scraper doctor`.
   The first run after install or update sets up a Python environment, which takes a minute.
   If a check fails, tell the user how to fix it and stop.
2. **Discover**: `recipe-scraper discover <site>`. For big sites, narrow it with
   `--url-pattern '<regex>'` (for example `/desserts/`) and/or `--since YYYY-MM-DD`.
   If the site refuses (HTTP 403, bot protection, robots.txt), tell the user it can't be scraped and stop.
   Never try to get around this: no other user agents, no WebFetch, no browser tools against that site.
3. **Sample**: `recipe-scraper run <site> --limit 20`, then `recipe-scraper show <site> -n 3` and show
   the user those recipes. Point out anything that looks wrong. Exports are cumulative per site, so if
   the site was scraped before, the files hold earlier recipes too.
4. **Estimate**: `recipe-scraper stats <site>`. The `remaining` block projects fetch minutes and Claude
   calls from the sample. Show this to the user, and if the estimate is more than 100 pages or 50 Claude
   calls, ask before going further. Offer `--max-llm-calls N` as a cap; work beyond the cap waits for
   the next run. Pass the same `--url-pattern` / `--since` to every command, not only to `discover`.
5. **Full run**: `recipe-scraper run <site>` with any filters and caps the user chose. Long runs
   (several hundred pages or more) belong in the background. When the user asks about progress, use
   `recipe-scraper stats <site>`.
6. **Deliver**: check `untitled` in `stats` first. If it is above zero, say so and offer
   `recipe-scraper titles <site>`, because those recipes carry the title the site published rather
   than a cleaned one. Then give the CSV and SQL paths from the export step. The SQL inserts only
   the source URLs the table does not have yet and never rewrites an existing row. `id` is left to
   the database and `transformed_recipe_id` is always empty.
7. **Push, only if the user asked for the recipes to go into Supabase**:
   - This writes to a table other people read. **Run `recipe-scraper push <site> --dry-run` first,
     show the user the counts it reports, and get their go-ahead before the real push.**
   - Then `recipe-scraper push <site>`. It skips source URLs the table already holds, so it is safe
     to re-run and it resumes if interrupted.
   - It needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the environment. If they are missing,
     tell the user to set them and stop. **Never ask the user to paste the key into the chat, never
     print it, and never put it on a command line** — `doctor` reports whether it works.
   - If the push is refused with PostgREST error `42501` or HTTP 401/403, the key is almost certainly
     the `anon` key rather than `service_role`. Say that; do not retry with anything else.
   - It refuses recipes whose titles were never cleaned. Run `titles` rather than reaching for
     `--allow-untitled`, unless the user says they want the published titles.

## Commands

| Command | Purpose |
|---|---|
| `recipe-scraper doctor [--no-llm]` | check Python, the claude CLI and its login, and the Supabase target |
| `recipe-scraper run <site> [--limit N] [--url-pattern R] [--since D] [--out DIR] [--table T]` | everything except push |
| `recipe-scraper discover <site> [--url-pattern R] [--since D]` | queue URLs from sitemaps |
| `recipe-scraper fetch <site> [--limit N] [--url-pattern R] [--since D]` | download queued pages |
| `recipe-scraper extract <site> [--limit N] [--url-pattern R] [--since D]` | extract recipes |
| `recipe-scraper titles <site>` | clean titles |
| `recipe-scraper export <site> [--out DIR] [--table T]` | write CSV and SQL |
| `recipe-scraper push <site> [--dry-run] [--limit N] [--table T] [--allow-untitled]` | insert into Supabase |
| `recipe-scraper stats <site>` | progress counters and remaining-work estimate |
| `recipe-scraper show <site> [-n N]` | the latest extracted recipes in short form |

Every command also accepts `--data-dir DIR` and `--max-llm-calls N`; the cap counts retries too.
The environment variables `MODEL_FAST` (default `haiku`), `MODEL_SMART` (default `sonnet`),
`LLM_CONCURRENCY` (default 3) and `REQUEST_DELAY` (default 1.0 seconds) tune it.

## Notes

- Don't read the HTML cache or the database into context. `stats` and the export files have what you need.
- `failed` pages keep their error in the database. A recipe page whose ingredients or steps came back
  empty is marked failed rather than silently skipped, and so is a page the parsers cannot read at all.
- Scraped recipe text and photos belong to their authors. Remind the user that the output is for
  personal use if they talk about republishing it.
