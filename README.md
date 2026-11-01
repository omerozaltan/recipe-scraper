# recipe-scraper

A Claude Code plugin that scrapes every recipe from a food website into CSV and upsert SQL.

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

| Column | Source |
|---|---|
| `title` | cleaned title ("The BEST Chewy Cookies (Video)" becomes "Chewy Cookies") |
| `original_name` | the title as published |
| `rating`, `votes` | schema.org aggregateRating (`rating` is empty when there are no votes) |
| `ingredients_raw` | one ingredient per line, group headers such as "For the frosting:" kept where the site has them |
| `instructions_raw` | numbered steps, section headers kept |
| `source_url` | page URL, unique key |
| `transformed_recipe_id` | always empty |
| `created_at` | when the recipe was first scraped (UTC) |

The SQL file creates a unique index on `source_url` and upserts, so importing it again never
duplicates rows.

## Behaviour

- Obeys robots.txt rules and Crawl-delay, and makes about one request per second.
- Sites that refuse automated requests (HTTP 403, bot protection) are reported and skipped, not worked around.
- State lives in `%LOCALAPPDATA%\recipe-scraper` (Windows), `~/Library/Application Support/recipe-scraper`
  (macOS) or `~/.local/share/recipe-scraper` (Linux), or in `--data-dir` / `RECIPE_DATA_DIR`.
  Every step resumes where it stopped.
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
claude --plugin-dir .              # load the plugin from this checkout for one session
claude plugin validate . --strict  # check plugin.json, marketplace.json and the skill
```
