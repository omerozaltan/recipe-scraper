"""robots.txt is the project's stated contract, so the edges of it are worth pinning."""

import httpx
import pytest

from recipe_scraper import config, fetch

ALLOW = "User-agent: *\nAllow: /\n"
DENY = "User-agent: *\nDisallow: /\n"


def _fetcher(routes: dict) -> fetch.Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        value = routes.get(str(request.url))
        if value is None:
            return httpx.Response(404)
        status, body = value
        if status in (301, 302):
            return httpx.Response(status, headers={"Location": body})
        return httpx.Response(status, text=body)

    f = fetch.Fetcher()
    f.client.close()
    f.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                            headers={"User-Agent": config.USER_AGENT})
    f.delay = 0
    return f


def test_a_redirect_to_another_host_rechecks_that_host_robots():
    """The client follows redirects, so the page that arrives can be one the destination
    host forbids -- and its robots.txt had never been read before the request."""
    f = _fetcher({
        "https://a.test/robots.txt": (200, ALLOW),
        "https://a.test/page": (301, "https://b.test/page"),
        "https://b.test/robots.txt": (200, DENY),
        "https://b.test/page": (200, "<html>ok</html>"),
    })
    with pytest.raises(fetch.RobotsDisallowed):
        f.get("https://a.test/page")


def test_a_redirect_to_an_allowed_url_is_followed():
    f = _fetcher({
        "https://a.test/robots.txt": (200, ALLOW),
        "https://a.test/old": (301, "https://a.test/new"),
        "https://a.test/new": (200, "<html>ok</html>"),
    })
    assert f.get("https://a.test/old").status_code == 200


def test_a_disallowed_url_is_never_requested():
    f = _fetcher({"https://a.test/robots.txt": (200, DENY)})
    with pytest.raises(fetch.RobotsDisallowed):
        f.get("https://a.test/secret")


def test_a_broken_robots_txt_is_treated_as_a_closed_door():
    """A 5xx means we do not know the rules; assuming "allowed" would be the wrong guess."""
    f = _fetcher({"https://a.test/robots.txt": (503, "down")})
    assert f.allowed("https://a.test/page") is False


def test_a_missing_robots_txt_means_everything_is_allowed():
    f = _fetcher({"https://a.test/robots.txt": (404, "no"), "https://a.test/page": (200, "ok")})
    assert f.allowed("https://a.test/page") is True
