"""Turn site titles into clean recipe titles with Claude, in batches."""

from pydantic import BaseModel

from . import claude_cli, config

SYSTEM = """You clean recipe titles scraped from food blogs.
Rules:
- Remove hype and marketing words: Best, The Best, Easy, Perfect, Ultimate, Amazing, Famous, Copycat, Homemade, Recipe.
- Remove parenthetical marketing, counts and emojis: "(One Bowl!)", "(Video)", "5-Ingredient".
- Keep words that describe the dish itself: Chewy, Brown Butter, Gluten-Free, Vegan, No-Bake, Mini.
- Use Title Case and keep the original language. Never translate.
- If a title is already clean, return it unchanged.
Return one entry per input index."""


class TitleItem(BaseModel):
    i: int
    title: str


class TitleBatch(BaseModel):
    titles: list[TitleItem]


def clean_batch(names: list[str]) -> list[str]:
    prompt = "\n".join(f"{i}: {name}" for i, name in enumerate(names))
    result = claude_cli.ask(task="titles", prompt=prompt, system=SYSTEM, schema=TitleBatch, model=config.MODEL_FAST)
    by_index = {item.i: item.title.strip() for item in result.titles}
    missing = [i for i in range(len(names)) if not by_index.get(i)]
    if missing:
        raise claude_cli.ClaudeError(f"titles missing for indexes {missing}")
    return [by_index[i] for i in range(len(names))]
