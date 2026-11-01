"""Deterministic extraction: schema.org Recipe (JSON-LD or microdata) plus WP Recipe Maker markup."""

import html as htmllib
import json
import re
from typing import Iterator

import extruct
from bs4 import BeautifulSoup

from .models import RecipeData

Group = tuple[str | None, list[str]]


def parse_page(html: str) -> RecipeData | None:
    """Return the page's recipe, or None when it has no usable schema.org Recipe."""
    found = find_recipe_schema(html)
    if found is None:
        return None
    recipe, syntax = found
    soup = BeautifulSoup(html, "lxml")

    ingredients = _wprm_ingredient_groups(soup) or [(None, [clean_text(i) for i in _as_list(recipe.get("recipeIngredient"))])]
    instructions = _jsonld_instruction_groups(recipe) or _wprm_instruction_groups(soup) or []
    name = clean_text(recipe.get("name")) or _og_title(soup)

    agg = recipe.get("aggregateRating") or {}
    votes = _to_int(agg.get("ratingCount") or agg.get("reviewCount")) or 0
    rating = _to_float(agg.get("ratingValue")) if votes else None

    ingredients_raw = format_groups(ingredients, numbered=False)
    instructions_raw = format_groups(instructions, numbered=True)
    if not (name and ingredients_raw and instructions_raw):
        return None
    return RecipeData(
        original_name=name,
        rating=rating,
        votes=votes,
        ingredients_raw=ingredients_raw,
        instructions_raw=instructions_raw,
        extraction_method=syntax,
        raw_jsonld=json.dumps(recipe, ensure_ascii=False),
    )


def format_groups(groups: list[Group], numbered: bool) -> str:
    """One item per line, group names as header lines, blank line between groups."""
    blocks, n = [], 0
    for name, items in groups:
        items = [i for i in items if i]
        if not items:
            continue
        lines = [name.rstrip(" :") + ":"] if name else []
        for item in items:
            n += 1
            lines.append(f"{n}. {item}" if numbered else item)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def clean_text(value) -> str:
    if not value:
        return ""
    text = htmllib.unescape(str(value))
    if "<" in text:
        text = BeautifulSoup(text, "lxml").get_text(" ")
    text = re.sub(r"[​-‍﻿]", "", text)  # zero-width characters
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    return text.replace("( ", "(").strip()


def find_recipe_schema(html: str) -> tuple[dict, str] | None:
    """Return (recipe, "jsonld" | "microdata"); extruct normalizes both to JSON-LD shape."""
    data = extruct.extract(html, syntaxes=["json-ld", "microdata"], uniform=True, errors="ignore")
    for syntax, key in (("jsonld", "json-ld"), ("microdata", "microdata")):
        for node in _walk(data.get(key, [])):
            types = node.get("@type")
            if types == "Recipe" or (isinstance(types, list) and "Recipe" in types):
                return node, syntax
    return None


def _walk(node) -> Iterator[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _walk(node["@graph"])


def _jsonld_instruction_groups(recipe: dict) -> list[Group]:
    raw = recipe.get("recipeInstructions")
    if isinstance(raw, str):
        return [(None, [clean_text(s) for s in re.split(r"\n+|<br\s*/?>|</p>", raw)])]
    groups: list[Group] = []
    loose: list[str] = []
    for item in _as_list(raw):
        if isinstance(item, dict) and item.get("@type") == "HowToSection":
            if loose:
                groups.append((None, loose))
                loose = []
            steps = [_step_text(s) for s in _as_list(item.get("itemListElement"))]
            groups.append((clean_text(item.get("name")) or None, steps))
        else:
            loose.append(_step_text(item))
    if loose:
        groups.append((None, loose))
    return groups


def _step_text(step) -> str:
    if isinstance(step, dict):
        return clean_text(step.get("text") or step.get("name"))
    return clean_text(step)


def _wprm_ingredient_groups(soup: BeautifulSoup) -> list[Group] | None:
    card = soup.select_one(".wprm-recipe-container")
    if card is None:
        return None
    groups: list[Group] = []
    for group in card.select(".wprm-recipe-ingredient-group"):
        items = []
        for li in group.select("li.wprm-recipe-ingredient"):
            for checkbox in li.select(".wprm-checkbox-container"):
                checkbox.decompose()
            items.append(clean_text(li.get_text(" ")))
        groups.append((_group_name(group), items))
    return groups if any(items for _, items in groups) else None


def _wprm_instruction_groups(soup: BeautifulSoup) -> list[Group] | None:
    card = soup.select_one(".wprm-recipe-container")
    if card is None:
        return None
    groups = [
        (_group_name(group), [clean_text(el.get_text(" ")) for el in group.select(".wprm-recipe-instruction-text")])
        for group in card.select(".wprm-recipe-instruction-group")
    ]
    return groups if any(items for _, items in groups) else None


def _group_name(group) -> str | None:
    el = group.select_one(".wprm-recipe-group-name")
    return clean_text(el.get_text(" ")) or None if el else None


def _og_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:title")
    return clean_text(og["content"]) if og and og.get("content") else ""


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
