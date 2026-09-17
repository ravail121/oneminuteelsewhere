import copy

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.prompts import EDITORIAL_RULES, story_prompt, system_prompt
from elsewhere.recurring_cast import CAST
from elsewhere.rubrics import CHILD_CATEGORY_RULES, RUBRICS


@pytest.mark.parametrize("category", list(RUBRICS))
def test_every_genre_has_its_own_child_first_story_direction(category):
    base = load_settings()
    settings = Settings(base.root, copy.deepcopy(base.raw))
    settings.raw["dashboard_brief"] = {"selected_category": category}
    prompt = story_prompt(settings, "short", [])
    assert CHILD_CATEGORY_RULES[category] in prompt
    assert "Children are the primary audience" in prompt
    assert "childlike, playful storytelling style" in prompt
    assert "who wants what, what goes wrong" in prompt
    assert "payoff clear immediately" in prompt
    assert "115-130" in prompt and "60 seconds" in prompt
    assert all(name in prompt for name in CAST)
    assert "Children are the primary audience" in system_prompt(settings)
    for other, direction in CHILD_CATEGORY_RULES.items():
        if other != category:
            assert direction not in prompt


def test_child_first_contract_covers_all_concrete_categories_without_conflicting_old_rule():
    assert set(CHILD_CATEGORY_RULES) == set(RUBRICS)
    assert "must not make the story childish" not in EDITORIAL_RULES
    assert "never robotic" in EDITORIAL_RULES
    assert "No documentary-style writing" in EDITORIAL_RULES
