import pytest

from recipe_scraper import config, db


@pytest.fixture
def data_dir(tmp_path):
    """Point the whole package at a throwaway data dir."""
    config.set_data_dir(tmp_path)
    db._initialised.clear()
    yield tmp_path
    db._initialised.clear()


RECIPE_HTML = """<html><head><title>t</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"WebPage","name":"not the recipe"},
{"@type":"Recipe","name":"The BEST Chewy Cookies (Video)",
 "recipeIngredient":["1 cup &amp; a bit flour","2 eggs","1 tsp\\u200b salt"],
 "recipeInstructions":[
   {"@type":"HowToSection","name":"For the dough:","itemListElement":[
      {"@type":"HowToStep","text":"Mix flour <b>well</b> ."},
      {"@type":"HowToStep","text":"Chill 1 hour"}]},
   {"@type":"HowToSection","name":"To bake","itemListElement":[
      {"@type":"HowToStep","text":"Bake 12 min"}]}],
 "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.666","ratingCount":"128"}}]}
</script></head><body></body></html>"""


@pytest.fixture(autouse=True)
def _reset_llm_budget():
    """--max-llm-calls is process-global; don't let one test's cap leak into the next."""
    from recipe_scraper import claude_cli
    claude_cli.set_budget(None)
    yield
    claude_cli.set_budget(None)
