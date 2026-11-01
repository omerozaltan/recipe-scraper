import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config, db, llm_extract, titles
from .claude_cli import BudgetExhausted, ClaudeError
from .discover import discover
from .export import export
from .extract import parse_page
from .fetch import Fetcher, RobotsDisallowed, fetch_page, html_path


def site_url(site: str) -> str:
    return site if "://" in site else "https://" + site


def site_key(site: str) -> str:
    return urlparse(site_url(site)).netloc.lower().removeprefix("www.")


def discover_step(site: str, url_pattern: str | None = None, since: str | None = None) -> None:
    print(f"[discover] {site_url(site)}")
    with Fetcher() as fetcher:
        urls, disallowed = discover(fetcher, site_url(site))
    total = len(urls)
    if url_pattern:
        regex = re.compile(url_pattern)
        urls = [(u, m) for u, m in urls if regex.search(u)]
    if since:
        # Pages without lastmod are kept: we can't tell their age.
        urls = [(u, m) for u, m in urls if m is None or m[:10] >= since]
    added, changed = db.upsert_pages(site_key(site), urls)
    print(f"[discover] {total} URLs in sitemaps ({disallowed} more disallowed by robots.txt)")
    if url_pattern or since:
        print(f"[discover] {len(urls)} match the filters")
    print(f"[discover] {added} new, {changed} changed since last run")


def fetch_step(site: str, limit: int | None) -> None:
    key = site_key(site)
    pages = db.pages_by_status(key, "discovered", limit)
    print(f"[fetch] {len(pages)} pages (~{len(pages) * config.REQUEST_DELAY:.0f}s at {config.REQUEST_DELAY}s/request)")
    with Fetcher() as fetcher:
        for n, page in enumerate(pages, 1):
            url = page["url"]
            try:
                path = fetch_page(fetcher, url)
                db.set_page(url, status="fetched", html_path=path.name, error=None)
            except RobotsDisallowed:
                db.set_page(url, status="skipped", error="disallowed by robots.txt")
            except httpx.HTTPStatusError as e:
                db.set_page(url, status="failed", error=f"HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                db.set_page(url, status="failed", error=f"{type(e).__name__}: {e}")
            if n % 10 == 0 or n == len(pages):
                print(f"[fetch] {n}/{len(pages)}")


def extract_step(site: str, limit: int | None) -> None:
    key = site_key(site)
    pages = db.pages_by_status(key, "fetched", limit)
    print(f"[extract] {len(pages)} pages")
    needs_llm = []
    for page in pages:
        rec = parse_page(html_path(page["url"]).read_text("utf-8"))
        if rec is None:
            needs_llm.append(page)
            continue
        db.save_recipe(key, page["url"], rec)
        db.set_page(page["url"], status="extracted", page_type="recipe", error=None)
    print(f"[extract] {len(pages) - len(needs_llm)} recipes from schema.org data, {len(needs_llm)} pages go to Claude")

    deferred = 0
    with ThreadPoolExecutor(config.LLM_CONCURRENCY) as pool:
        futures = {pool.submit(llm_extract.process, p["url"], str(html_path(p["url"]))): p["url"] for p in needs_llm}
        for n, future in enumerate(as_completed(futures), 1):
            url = futures[future]
            try:
                page_type, rec = future.result()
            except BudgetExhausted:
                deferred += 1  # stays 'fetched' and is picked up by the next run
                continue
            except ClaudeError as e:
                db.set_page(url, status="failed", error=str(e)[:500])
                print(f"[extract] {n}/{len(futures)} FAILED {url}: {e}")
                continue
            if rec is not None:
                db.save_recipe(key, url, rec)
                db.set_page(url, status="extracted", page_type=page_type, error=None)
                outcome = "saved"
            elif page_type == "recipe":
                db.set_page(url, status="failed", page_type=page_type,
                            error="classified as recipe but ingredients or steps came back empty")
                outcome = "FAILED (incomplete)"
            else:
                db.set_page(url, status="skipped", page_type=page_type, error=None)
                outcome = "skipped"
            print(f"[extract] {n}/{len(futures)} {page_type:<8} {outcome}  {url}")
    if deferred:
        print(f"[extract] LLM call budget reached: {deferred} pages left for the next run")


def titles_step(site: str) -> None:
    key = site_key(site)
    rows = db.recipes_without_title(key)
    batches = [rows[i:i + config.TITLE_BATCH_SIZE] for i in range(0, len(rows), config.TITLE_BATCH_SIZE)]
    print(f"[titles] {len(rows)} recipes in {len(batches)} Claude calls")
    with ThreadPoolExecutor(config.LLM_CONCURRENCY) as pool:
        futures = {pool.submit(titles.clean_batch, [r["original_name"] for r in b]): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                cleaned = future.result()
            except BudgetExhausted:
                continue
            except ClaudeError as e:
                print(f"[titles] batch of {len(batch)} failed: {e}")
                continue
            db.set_titles([(r["source_url"], t) for r, t in zip(batch, cleaned)])
    print(f"[titles] done, {len(db.recipes_without_title(key))} still untitled")


def export_step(site: str, table: str, out_dir: Path | None) -> None:
    csv_path, sql_path, count, problems = export(site_key(site), out_dir or config.EXPORT_DIR, table)
    for p in problems:
        print(f"[export] skipped {p}")
    print(f"[export] {count} recipes ->\n  {csv_path}\n  {sql_path}")


def estimate(site: str) -> dict:
    """Project remaining work from what earlier (e.g. --limit) runs on this site measured."""
    key = site_key(site)
    s = db.stats(key)
    pending_fetch = s["pages"].get("discovered", 0)
    pending_extract = s["pages"].get("fetched", 0)
    processed = sum(s["pages"].get(k, 0) for k in ("extracted", "skipped", "failed"))
    llm_pages = sum(v for k, v in s["page_types"].items()) - s["recipes"].get("jsonld", 0) - s["recipes"].get("microdata", 0)
    llm_share = llm_pages / processed if processed else None
    remaining = pending_fetch + pending_extract
    return {
        "pages_to_fetch": pending_fetch,
        "pages_to_extract": remaining,
        "fetch_minutes": round(pending_fetch * config.REQUEST_DELAY / 60, 1),
        "share_needing_claude": None if llm_share is None else round(llm_share, 2),
        # classification is 1 call; a recipe without schema data costs a second (extraction) call
        "claude_calls_estimate": None if llm_share is None else round(remaining * llm_share * 1.5 + remaining / config.TITLE_BATCH_SIZE),
        "basis": f"{processed} pages processed so far" if processed else "run a small --limit sample first",
    }
