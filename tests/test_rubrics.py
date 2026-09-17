"""Category contracts and review gates; no model requests or generated content."""
import copy
from pathlib import Path

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.models import (
    CategoryScore,
    EditorialScores,
    StoryPackage,
    StoryReview,
    StoryReviewResponse,
)
from elsewhere.prompts import (
    EDITORIAL_RULES,
    SYSTEM_PROMPT,
    review_prompt,
    story_prompt,
)
from elsewhere.rubrics import (
    DEFAULT_CATEGORY,
    GLOBAL_SCORES,
    RUBRICS,
    estimated_seconds,
    selected_category,
)
from elsewhere.safety import SafetyError, local_checks, review_checks

ROOT = Path(__file__).resolve().parents[1]


def config(category):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    raw["dashboard_brief"] = {"selected_category": category, "story_type": category}
    return Settings(ROOT, raw)


def approved(category):
    return StoryReview(approved=True, score=9, policy_risk="low", originality_risk="low",
        category_scores=EditorialScores(**dict.fromkeys(GLOBAL_SCORES, 9)), reviewed_category=category,
        category_specific_scores=[CategoryScore(name=k, score=9) for k in RUBRICS[category][1]],
        evidence="Mock assessment of the opening, connected events, language, category and ending; no API request.")


@pytest.mark.parametrize("category", list(RUBRICS))
def test_generation_and_review_use_only_selected_rubric(category):
    settings = config(category)
    fixture = (ROOT / "stories/wedding-photo-test.json").read_text()
    generation = story_prompt(settings, "short", [])
    review = review_prompt(settings, fixture)
    for prompt in (generation, review):
        assert f"Selected category: {category}." in prompt
        assert RUBRICS[category][0] in prompt
        for other in RUBRICS:
            if other != category:
                assert RUBRICS[other][0] not in prompt
        assert all(name in prompt for name in GLOBAL_SCORES)
        assert all(name in prompt for name in RUBRICS[category][1])
    assert "115-130" in generation and "60 seconds" in generation


@pytest.mark.parametrize("category", list(RUBRICS))
def test_valid_review_needs_no_unrelated_scores(category):
    review = approved(category)
    review_checks(review, config(category))
    assert set(review.category_scores.model_dump()) == set(GLOBAL_SCORES)
    assert {s.name for s in review.category_specific_scores} == set(RUBRICS[category][1])


@pytest.mark.parametrize("category,criterion", [(c, k) for c in RUBRICS for k in RUBRICS[c][1]])
def test_each_relevant_score_below_eight_blocks_approval(category, criterion):
    review = approved(category)
    next(s for s in review.category_specific_scores if s.name == criterion).score = 7
    with pytest.raises(SafetyError, match=criterion):
        review_checks(review, config(category))


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_category"])
def test_wrong_or_incomplete_rubric_is_rejected(mutation):
    review = approved("Funny")
    if mutation == "missing":
        review.category_specific_scores.pop()
    elif mutation == "extra":
        review.category_specific_scores.append(CategoryScore(name="danger", score=10))
    elif mutation == "duplicate":
        review.category_specific_scores.append(review.category_specific_scores[0])
    else:
        review.reviewed_category = "Horror"
    with pytest.raises(SafetyError, match="category"):
        review_checks(review, config("Funny"))


def test_global_contract_does_not_impose_suspense_formula():
    for text in (EDITORIAL_RULES, SYSTEM_PROMPT):
        for obsolete in ("personal danger", "THREE escalating", "first THREE seconds", "final FIVE TO EIGHT", "one invented"):
            assert obsolete not in text
    assert "Personal danger and suspense are not required" in story_prompt(config("Funny"), "short", [])
    assert "A dark twist is not required" in story_prompt(config("Inspirational"), "short", [])
    assert "A twist is optional" in story_prompt(config("Emotional"), "short", [])


def test_random_must_be_resolved_before_prompt_or_request():
    with pytest.raises(ValueError, match="resolved and saved"):
        story_prompt(config("Random"), "short", [])


def test_story_category_mismatch_blocks_locally():
    story = StoryPackage.model_validate_json((ROOT / "stories/wedding-photo-test.json").read_text())
    story.story_category = "Horror"
    with pytest.raises(SafetyError, match="category does not match"):
        local_checks(story, config("Funny"), [])


def test_short_duration_cap_even_with_relaxed_old_word_config():
    settings = config(DEFAULT_CATEGORY)
    settings.raw["formats"]["short"]["narration_words_max"] = 200
    story = StoryPackage.model_validate_json((ROOT / "stories/wedding-photo-test.json").read_text())
    story.scenes[-1].narration += " I ran home." * 5
    story.narration = " ".join(s.narration for s in story.scenes)
    assert estimated_seconds(135) == 60
    assert estimated_seconds(130) < 60
    with pytest.raises(SafetyError, match="duration exceeds 60"):
        local_checks(story, settings, [])


def test_legacy_scores_readable_but_cannot_approve_new_category():
    data = approved(DEFAULT_CATEGORY).model_dump()
    data["category_scores"] = dict.fromkeys(["simple_language", "opening_hook", "suspense_growth", "emotional_stakes", "final_twist"], 9)
    review = StoryReview.model_validate(data)
    with pytest.raises(SafetyError, match="five editorial"):
        review_checks(review, config(DEFAULT_CATEGORY))


def test_new_review_api_schema_is_closed_and_excludes_legacy_scores():
    from openai.lib._pydantic import to_strict_json_schema
    schema = to_strict_json_schema(StoryReviewResponse)

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(schema)
    assert "LegacyEditorialScores" not in str(schema)
    assert selected_category(config("Science fiction")) == "Science Fiction"
