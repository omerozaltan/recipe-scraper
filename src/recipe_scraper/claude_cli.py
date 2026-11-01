"""Bridge to the local `claude` CLI in headless (-p) mode.

Every call runs with all tools disabled and from an empty temp directory: the
page text we send is untrusted, so a prompt injection in it can't run commands
or read files, and no project CLAUDE.md gets loaded.
"""

import hashlib
import json
import subprocess
import tempfile
import threading
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config, db

T = TypeVar("T", bound=BaseModel)


class ClaudeError(RuntimeError):
    pass


class BudgetExhausted(ClaudeError):
    """The --max-llm-calls limit for this run was reached; the work is left for a later run."""


_budget_lock = threading.Lock()
_calls_left: int | None = None  # None means unlimited


def set_budget(max_calls: int | None) -> None:
    global _calls_left
    _calls_left = max_calls


def _take_budget() -> None:
    global _calls_left
    with _budget_lock:
        if _calls_left is None:
            return
        if _calls_left <= 0:
            raise BudgetExhausted("LLM call budget for this run is used up")
        _calls_left -= 1


def ask(task: str, prompt: str, system: str, schema: type[T], model: str, retries: int = 2) -> T:
    schema_json = json.dumps(schema.model_json_schema())
    key = hashlib.sha256("\0".join([model, system, schema_json, prompt]).encode()).hexdigest()
    cache_file = config.LLM_CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        return schema.model_validate_json(cache_file.read_text("utf-8"))

    _take_budget()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            data, meta = _run(prompt, system, schema_json, model)
            result = schema.model_validate(data)
        except (ClaudeError, ValidationError, subprocess.TimeoutExpired) as e:
            last_error = e
            db.log_llm_call(task, model, ok=False, error=str(e)[:500])
            time.sleep(5 * (attempt + 1))
            continue
        duration = meta.get("duration_ms") or int((time.monotonic() - started) * 1000)
        db.log_llm_call(task, model, ok=True, duration_ms=duration, cost_usd=meta.get("total_cost_usd"))
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(result.model_dump_json(), "utf-8")
        return result
    raise ClaudeError(f"{task} failed after {retries + 1} attempts: {last_error}")


def _run(prompt: str, system: str, schema_json: str, model: str) -> tuple[dict, dict]:
    cmd = [
        config.CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        # Skip the user's settings, plugins (this one included) and MCP servers: faster
        # startup, and no hooks or skills leak into what is a plain extraction call.
        "--setting-sources=",
        "--strict-mcp-config",
        "--system-prompt", system,
        "--json-schema", schema_json,
    ]
    with tempfile.TemporaryDirectory(prefix="recipe-llm-") as cwd:
        # The prompt goes through stdin: Windows caps the command line at ~32K chars.
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", timeout=config.LLM_TIMEOUT)
    if proc.returncode != 0:
        raise ClaudeError(f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"unparseable CLI output: {proc.stdout[:300]}") from e
    if out.get("is_error") or out.get("structured_output") is None:
        raise ClaudeError(f"{out.get('subtype')}: {str(out.get('result'))[:300]}")
    return out["structured_output"], out
