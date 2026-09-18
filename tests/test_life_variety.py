import copy

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.dashboard_store import DashboardStore, NewVideo
from elsewhere.prompts import story_prompt, system_prompt
from elsewhere.recurring_cast import (
    CAST,
    LIFE_VARIETY,
    RULES,
    STYLE,
    VISUAL_IDENTITIES,
    contract,
)
from elsewhere.rubrics import RUBRICS


@pytest.mark.parametrize("category", list(RUBRICS))
def test_new_stories_keep_selected_category_and_vary_fictional_lives(category):
    base = load_settings()
    settings = Settings(base.root, copy.deepcopy(base.raw))
    settings.raw["dashboard_brief"] = {"selected_category": category}
    for prompt in (system_prompt(settings), story_prompt(settings, "short", [])):
        assert LIFE_VARIETY in prompt
        assert all(name in prompt for name in CAST)
        assert "young adults, middle-aged or elderly" in prompt
        assert "Repeat the COMPLETE chosen character description(s)" in prompt
        assert "Keep the USER-SELECTED story category" in prompt
        assert "Never rewrite an already approved story" in prompt
    assert f"Set story_category to {category!r}" in story_prompt(settings, "short", [])
    assert "VERY catchy and clickable" in story_prompt(settings, "short", [])
    assert "never the exact words \"subscribe to\"" in story_prompt(settings, "short", [])


def test_old_settings_keep_their_existing_cast_contract():
    base = load_settings()
    settings = Settings(base.root, copy.deepcopy(base.raw))
    settings.raw["recurring_cast"].pop("life_variety_version", None)
    assert contract(settings) == RULES + "\n" + VISUAL_IDENTITIES + "\nStyle: " + STYLE


def test_variety_does_not_enable_cast_on_legacy_non_cast_projects():
    base = load_settings()
    settings = Settings(base.root, copy.deepcopy(base.raw))
    settings.raw.pop("recurring_cast")
    assert contract(settings) == ""


def test_new_project_saves_variety_setting_and_restart_preserves_it(tmp_path):
    base = load_settings()
    settings = Settings(tmp_path, copy.deepcopy(base.raw))
    store = DashboardStore(settings, synchronous=True)
    project_id = store.create(NewVideo(), "a" * 64)
    project = store.load(project_id)
    assert project["recurring_cast"]["life_variety_version"] == 1
    assert LIFE_VARIETY in contract(store.settings(project))
    restarted = DashboardStore(settings, synchronous=True)
    assert LIFE_VARIETY in contract(restarted.settings(restarted.load(project_id)))
    assert not (store.revision_dir(project) / "draft.json").exists()
