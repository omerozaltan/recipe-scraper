"""The deterministic path: schema.org data, no Claude involved."""

from recipe_scraper import extract

from .conftest import RECIPE_HTML


def test_reads_recipe_out_of_a_graph():
    rec = extract.parse_page(RECIPE_HTML)
    assert rec is not None
    assert rec.extraction_method == "jsonld"
    assert rec.original_name == "The BEST Chewy Cookies (Video)"  # cleaned later, by `titles`
    assert rec.rating == 4.67 and rec.votes == 128


def test_unescapes_entities_and_strips_zero_width_characters():
    rec = extract.parse_page(RECIPE_HTML)
    assert rec is not None
    assert rec.ingredients_raw.splitlines() == ["1 cup & a bit flour", "2 eggs", "1 tsp salt"]


def test_keeps_section_names_and_numbers_steps_across_sections():
    rec = extract.parse_page(RECIPE_HTML)
    assert rec is not None
    assert rec.instructions_raw == (
        "For the dough:\n1. Mix flour well.\n2. Chill 1 hour\n\nTo bake:\n3. Bake 12 min"
    )


def test_rating_is_dropped_when_nothing_voted():
    rec = extract.parse_page(RECIPE_HTML.replace('"ratingCount":"128"', '"ratingCount":"0"'))
    assert rec is not None
    assert rec.rating is None and rec.votes == 0


def test_recipe_without_steps_is_left_to_the_llm_path():
    assert extract.parse_page(RECIPE_HTML.replace('"recipeInstructions"', '"xInstructions"')) is None


def test_page_with_no_recipe_markup_at_all():
    assert extract.parse_page("<html><body><p>hello</p></body></html>") is None
