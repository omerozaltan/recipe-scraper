"""The call budget is the only cost control the tool offers, so it has to be honest."""

import pytest
from pydantic import BaseModel

from recipe_scraper import claude_cli


class Answer(BaseModel):
    ok: bool


def _ask():
    return claude_cli.ask(task="t", prompt="p", system="s", schema=Answer, model="haiku")


@pytest.fixture
def failing_cli(monkeypatch):
    attempts = []

    def boom(*args, **kwargs):
        attempts.append(1)
        raise claude_cli.ClaudeError("no")

    monkeypatch.setattr(claude_cli, "_run", boom)
    monkeypatch.setattr(claude_cli.time, "sleep", lambda _s: None)
    return attempts


def test_every_retry_costs_a_call(data_dir, failing_cli):
    """Each attempt is its own `claude -p` process. Charging only the first let a run
    start up to three times as many processes as --max-llm-calls allowed."""
    claude_cli.set_budget(2)
    with pytest.raises(claude_cli.BudgetExhausted):
        _ask()
    assert len(failing_cli) == 2


def test_without_a_budget_the_retries_still_stop_at_three(data_dir, failing_cli):
    with pytest.raises(claude_cli.ClaudeError):
        _ask()
    assert len(failing_cli) == 3


def test_a_cached_answer_costs_nothing(data_dir, monkeypatch):
    monkeypatch.setattr(claude_cli, "_run", lambda *a, **k: ({"ok": True}, {}))
    assert _ask().ok is True
    claude_cli.set_budget(0)
    assert _ask().ok is True  # served from the cache, so the empty budget is not touched
