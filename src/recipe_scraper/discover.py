"""Find candidate recipe URLs through the site's sitemaps."""

import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel

from . import claude_cli, config
from .fetch import Fetcher, RobotsDisallowed

SITEMAP_CANDIDATES = ["/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/recipe-sitemap.xml"]
SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".xml")


class SitemapChoice(BaseModel):
    recipe_sitemaps: list[str]


def discover(fetcher: Fetcher, site_url: str) -> tuple[list[tuple[str, str | None]], int]:
    """Return ([(url, lastmod)], number of URLs dropped because robots.txt disallows them)."""
    root = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    host = urlparse(root).netloc.removeprefix("www.")
    urls: dict[str, str | None] = {}
    disallowed = 0
    for sitemap in _root_sitemaps(fetcher, root):
        for url, lastmod in _walk(fetcher, sitemap, depth=0):
            parsed = urlparse(url)
            if (parsed.netloc.removeprefix("www.") != host or parsed.path in ("", "/")
                    or parsed.path.lower().endswith(SKIP_EXTENSIONS)):
                continue
            if not fetcher.allowed(url):
                disallowed += 1
                continue
            urls[url] = lastmod
    if not urls:
        raise RuntimeError(f"no crawlable URLs found in sitemaps of {root}")
    return list(urls.items()), disallowed


def _root_sitemaps(fetcher: Fetcher, root: str) -> list[str]:
    errors = []
    robots = fetcher.robots(root)
    if robots.disallow_all:
        raise RuntimeError(f"robots.txt of {root} disallows crawling (or could not be read); skipping this site")
    if listed := robots.site_maps():
        return listed
    for path in SITEMAP_CANDIDATES:
        url = urljoin(root, path)
        try:
            fetcher.get(url)
            return [url]
        except (httpx.HTTPError, RobotsDisallowed) as e:
            errors.append(_describe(e))
    if errors and all("403" in e for e in errors):
        raise RuntimeError(f"{root} refuses automated requests (HTTP 403 on sitemaps); skipping this site")
    raise RuntimeError(f"no sitemap found for {root} ({'; '.join(sorted(set(errors)))})")


def _describe(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, RobotsDisallowed):
        return "disallowed by robots.txt"
    return type(e).__name__


def _walk(fetcher: Fetcher, sitemap_url: str, depth: int) -> list[tuple[str, str | None]]:
    try:
        root = ET.fromstring(fetcher.get(sitemap_url).content)
    except (httpx.HTTPError, RobotsDisallowed, ET.ParseError) as e:
        print(f"  skipping sitemap {sitemap_url}: {_describe(e)}")
        return []
    entries = [(_child_text(el, "loc"), _child_text(el, "lastmod")) for el in root if _local(el.tag) in ("sitemap", "url")]
    entries = [(loc, lastmod) for loc, lastmod in entries if loc]
    if _local(root.tag) != "sitemapindex":
        return entries
    if depth > 2:
        return []
    children = _pick_recipe_sitemaps([loc for loc, _ in entries])
    names = [c.rsplit("/", 1)[-1] for c in children]
    print(f"  {len(names)} sitemaps used: {', '.join(names[:3])}{', ...' if len(names) > 3 else ''}")
    result = []
    for child in children:
        result.extend(_walk(fetcher, child, depth + 1))
    return result


def _pick_recipe_sitemaps(sitemaps: list[str]) -> list[str]:
    obvious = [s for s in sitemaps if any(k in s.lower() for k in ("recipe", "post"))]
    if obvious or len(sitemaps) == 1:
        return obvious or sitemaps
    choice = claude_cli.ask(
        task="pick_sitemaps",
        prompt="Sub-sitemaps of a food blog:\n" + "\n".join(sitemaps),
        system=("You pick which sub-sitemaps of a recipe website most likely list individual recipe pages. "
                "Exclude taxonomy (category, tag, author), media, and static pages. "
                "Return URLs exactly as given."),
        schema=SitemapChoice,
        model=config.MODEL_FAST,
    )
    picked = [s for s in choice.recipe_sitemaps if s in sitemaps]
    return picked or sitemaps


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return None
