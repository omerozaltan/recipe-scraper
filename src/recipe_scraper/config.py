import os
import shutil
import sys
from pathlib import Path

VERSION = "0.2.0"


def _default_data_dir() -> Path:
    if env := os.environ.get("RECIPE_DATA_DIR"):
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "recipe-scraper"


def set_data_dir(path: Path) -> None:
    global DATA_DIR, DB_PATH, HTML_DIR, LLM_CACHE_DIR, EXPORT_DIR
    DATA_DIR = Path(path)
    DB_PATH = DATA_DIR / "recipes.db"
    HTML_DIR = DATA_DIR / "html"
    LLM_CACHE_DIR = DATA_DIR / "llm_cache"
    EXPORT_DIR = DATA_DIR / "export"


set_data_dir(_default_data_dir())


def _find_claude() -> str:
    if env := os.environ.get("CLAUDE_BIN"):
        return env
    if found := shutil.which("claude"):
        return found
    return str(Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude"))


CLAUDE_BIN = _find_claude()

ROBOTS_AGENT = "recipe-scraper"  # the token matched against robots.txt User-agent lines
USER_AGENT = os.environ.get(
    "RECIPE_USER_AGENT",
    f"Mozilla/5.0 (compatible; {ROBOTS_AGENT}/{VERSION}; +https://github.com/omerozaltan/recipe-scraper)",
)
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "1.0"))  # seconds between HTTP requests
REQUEST_TIMEOUT = 30

MODEL_FAST = os.environ.get("MODEL_FAST", "haiku")  # classification, titles, sitemap picking
MODEL_SMART = os.environ.get("MODEL_SMART", "sonnet")  # full recipe extraction
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "3"))
LLM_TIMEOUT = 300
LLM_PAGE_CHARS = 20_000  # page text sent to Claude is truncated to this
TITLE_BATCH_SIZE = 25
