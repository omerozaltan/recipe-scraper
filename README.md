# recipe-scraper

A Claude Code plugin that scrapes every recipe from a food website into CSV, insert SQL, or
straight into a Supabase table.

It reads schema.org recipe data (JSON-LD or microdata) wherever a site publishes it, and uses Claude
only for what rules can't handle: picking the right sitemaps, classifying pages without recipe data,
extracting those recipes, and cleaning titles. Claude calls run through your own Claude Code login
(`claude -p`), so no API key is needed. They count toward your usage.

## Install

Requirements: Claude Code and Python 3.11+.

```
/plugin marketplace add omerozaltan/recipe-scraper
/plugin install recipe-scraper@recipe-tools
```

Then ask Claude, for example: `/recipe-scraper:scrape https://handletheheat.com`, or just
"scrape the recipes from handletheheat.com". Claude checks your setup, runs a 20-page sample,
shows you the results and an estimate, and asks before the full run.

The first command after installing sets up a private Python environment, which takes about a minute.

## Output

Both the CSV and the SQL have the shape of a `raw_recipes` table: scraped recipes as they
arrived, for someone to review and transform later.

| Column | Source |
|---|---|
| `title` | cleaned title ("The BEST Chewy Cookies (Video)" becomes "Chewy Cookies") |
| `original_name` | the title as published |
| `rating`, `votes` | schema.org aggregateRating (`rating` is empty when there are no votes) |
| `ingredients_raw` | one ingredient per line, group headers such as "For the frosting:" kept where the site has them |
| `instructions_raw` | numbered steps, section headers kept |
| `source_url` | page URL, the key rows are matched on |
| `transformed_recipe_id` | always empty |
| `created_at` | when the recipe was first scraped (UTC) |

A recipe whose title has not been cleaned yet is exported under the title the site published,
and the export says how many. It is never dropped: re-run `titles` and export again to clean it.

The SQL inserts only the source URLs the table does not already have. It is not an upsert — a
row someone may already have worked from is never rewritten — and it needs no unique index, so
importing changes nothing about your schema.

## Sending recipes to Supabase

`recipe-scraper push <site>` inserts the recipes into a Supabase table over PostgREST instead
of writing a file.

```
export SUPABASE_URL=https://xxxx.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=...
recipe-scraper push https://handletheheat.com --dry-run   # report, send nothing
recipe-scraper push https://handletheheat.com
```

- The key has to be the **service_role** key. On a stock Supabase the `anon` key is granted
  SELECT but not INSERT, so a push with it fails with PostgREST error `42501`. The key is read
  from the environment, never logged, and never stored in the local database.
- Rows are matched on `source_url`: a URL the table already holds is not sent again and not
  overwritten, so a push can be repeated and resumes after an interruption.
- `--table` names the target (default `raw_recipes`). `id` and `transformed_recipe_id` are left
  to the table's own defaults.
- Recipes with uncleaned titles are refused unless you pass `--allow-untitled`, because the
  target column is usually NOT NULL and a shared table is a poor place for "The BEST (Video)".
- `run` stops before pushing. Sending recipes to a shared table is a separate command on purpose.

`recipe-scraper doctor` reports whether the target is configured and readable.

## Behaviour

- Obeys robots.txt rules and Crawl-delay, and makes about one request per second. Redirects are
  re-checked against the destination's robots.txt, which may be a host whose rules we had not read.
- Sites that refuse automated requests (HTTP 403, bot protection) are reported and skipped, not worked around.
- State lives in `%LOCALAPPDATA%\recipe-scraper` (Windows), `~/Library/Application Support/recipe-scraper`
  (macOS) or `~/.local/share/recipe-scraper` (Linux), or in `--data-dir` / `RECIPE_DATA_DIR`.
  Every step resumes where it stopped, and a page that cannot be parsed is marked failed rather
  than stopping the step.
- `--url-pattern` and `--since` apply to every step, not only to `discover`, so narrowing a run
  also narrows a queue an earlier, wider `discover` already filled.
- `--max-llm-calls` counts every `claude -p` process, retries included.
- Not supported yet: sites without a sitemap, and sites that render recipes with JavaScript.

Scraped recipe text and photos belong to their authors. Use the output for personal purposes.

## Using it without Claude Code's plugin system

```
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip install -e .
.venv/bin/python -m recipe_scraper doctor
.venv/bin/python -m recipe_scraper run https://handletheheat.com --limit 20
```

Run `recipe-scraper --help` for every command and option.

## Development

```
pip install -e '.[dev]'
pytest                             # the suite covers the deterministic path and every step's contract
claude --plugin-dir .              # load the plugin from this checkout for one session
claude plugin validate . --strict  # check plugin.json, marketplace.json and the skill
```
