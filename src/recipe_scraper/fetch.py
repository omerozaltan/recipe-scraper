import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import config


class RobotsDisallowed(Exception):
    pass


class Fetcher:
    """Polite HTTP client: obeys robots.txt (rules and Crawl-delay), rate-limits, retries on 429/5xx."""

    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
            timeout=config.REQUEST_TIMEOUT,
        )
        self.delay = config.REQUEST_DELAY
        self._last_request = 0.0
        self._robots: dict[str, RobotFileParser] = {}

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.client.close()

    def robots(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                resp = self._send(origin + "/robots.txt")
                if resp.status_code >= 500:
                    rp.disallow_all = True  # server error: assume everything is off limits
                elif resp.status_code >= 400:
                    rp.allow_all = True  # no robots.txt: everything is allowed
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError:
                rp.disallow_all = True
            if (crawl_delay := rp.crawl_delay(config.ROBOTS_AGENT)) is not None:
                self.delay = max(self.delay, float(crawl_delay))
            self._robots[origin] = rp
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(config.ROBOTS_AGENT, url)

    def get(self, url: str, attempts: int = 3) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        for attempt in range(attempts):
            try:
                resp = self._send(url)
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < attempts - 1:
                    time.sleep(float(resp.headers.get("Retry-After", 10 * (attempt + 1))))
                    continue
            resp.raise_for_status()
            return resp
        raise AssertionError("unreachable")

    def _send(self, url: str) -> httpx.Response:
        wait = self._last_request + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        return self.client.get(url)


def html_path(url: str) -> Path:
    return config.HTML_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.html"


def fetch_page(fetcher: Fetcher, url: str) -> Path:
    resp = fetcher.get(url)
    path = html_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, "utf-8")
    return path
