"""Plugin entry point: build a private venv on first use, then run the CLI inside it.

Standard library only, since it runs on whatever Python the user has before any
dependency is installed. The venv is rebuilt when the plugin moves (a plugin update
installs to a new directory) or when pyproject.toml changes.
"""

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    # Keep in sync with recipe_scraper.config._default_data_dir.
    if env := os.environ.get("RECIPE_DATA_DIR"):
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "recipe-scraper"


def ensure_venv() -> Path:
    env_dir = data_dir() / "venv"
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    stamp_file = env_dir / ".installed-from"
    stamp = hashlib.sha256(str(ROOT).encode() + (ROOT / "pyproject.toml").read_bytes()).hexdigest()
    if python.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return python

    print("recipe-scraper: setting up its Python environment (first run after install or update)...", file=sys.stderr)
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env_dir)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", str(ROOT)],
        check=True,
        stdout=sys.stderr,
    )
    stamp_file.write_text(stamp)
    return python


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"recipe-scraper needs Python 3.11+, found {sys.version.split()[0]}")
    try:
        python = ensure_venv()
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"recipe-scraper: could not set up its Python environment: {e}")
    sys.exit(subprocess.run([str(python), "-m", "recipe_scraper", *sys.argv[1:]]).returncode)


if __name__ == "__main__":
    main()
