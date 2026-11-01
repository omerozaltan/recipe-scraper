"""Environment checks, so problems show up before a long run rather than in the middle of it."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

from . import claude_cli, config


class Pong(BaseModel):
    ok: bool


def run(check_llm: bool = True) -> bool:
    ok = True

    def report(passed: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'ok' if passed else 'FAIL'}] {label}{': ' + detail if detail else ''}")

    report(sys.version_info >= (3, 11), "python", sys.version.split()[0])

    binary = Path(config.CLAUDE_BIN)
    found = binary.exists() or shutil.which(config.CLAUDE_BIN) is not None
    report(found, "claude binary", config.CLAUDE_BIN if found else f"not found at {config.CLAUDE_BIN}; set CLAUDE_BIN")
    if found:
        try:
            out = subprocess.run([config.CLAUDE_BIN, "--version"], capture_output=True, text=True, timeout=30)
            report(out.returncode == 0, "claude version", out.stdout.strip() or out.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            report(False, "claude version", str(e))

    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.DATA_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        report(True, "data dir", str(config.DATA_DIR))
    except OSError as e:
        report(False, "data dir", f"{config.DATA_DIR}: {e}")

    if check_llm and found:
        try:
            # Straight to the CLI: a health check must bypass the response cache and the call budget.
            reply, _ = claude_cli._run("Health check. Set ok to true.", "Answer the health check.",
                                       json.dumps(Pong.model_json_schema()), config.MODEL_FAST)
            report(Pong.model_validate(reply).ok, "claude -p call", f"model {config.MODEL_FAST}, logged in")
        except Exception as e:  # noqa: BLE001 - any failure here is the finding
            report(False, "claude -p call", f"{e} (is Claude Code logged in? run `claude` once and sign in)")

    if os.environ.get("CLAUDECODE"):
        print("[info] running inside a Claude Code session; scraper calls start separate `claude -p` processes")
    return ok
