from pathlib import Path

import pytest

from elsewhere.config import load_settings
from elsewhere.demo import (
    curated_story,
    demo_review,
    demo_story,
    eighth_shadow_final_story,
    eighth_shadow_story,
)
from elsewhere.safety import SafetyError, local_checks, review_checks, similarity

ROOT = Path(__file__).resolve().parents[1]


def test_historical_demo_remains_structurally_valid():
    settings = load_settings(ROOT / "config.yaml")
    local_checks(demo_story(), settings, [], enforce_editorial=False)


def test_old_technical_story_rejected_by_new_editorial_rules():
    settings = load_settings(ROOT / "config.yaml")
    story = curated_story()
    with pytest.raises(SafetyError):
        local_checks(story, settings, [], expected_format="short")
    assert story.word_count == 137
    assert len(story.scenes) == 8


def test_old_shadow_story_rejected_by_new_editorial_rules():
    settings = load_settings(ROOT / "config.yaml")
    story = eighth_shadow_story()
    with pytest.raises(SafetyError):
        local_checks(story, settings, [], expected_format="short")
    assert 125 <= story.word_count <= 145
    assert len(story.scenes) == 8


def test_old_shadow_final_story_rejected_by_new_editorial_rules():
    settings = load_settings(ROOT / "config.yaml")
    story = eighth_shadow_final_story()
    with pytest.raises(SafetyError):
        local_checks(story, settings, [], expected_format="short")
    assert 125 <= story.word_count <= 145
    assert len(story.scenes) == 8


def test_blocked_reference_is_rejected():
    settings = load_settings(ROOT / "config.yaml")
    story = demo_story()
    story.scenes[0].narration += " Harry Potter appeared."
    with pytest.raises(SafetyError):
        local_checks(story, settings, [])


def test_similarity_is_case_insensitive():
    assert similarity("The Last Mirror", "the last mirror") == 1.0


def test_requested_format_mismatch_is_rejected():
    settings = load_settings(ROOT / "config.yaml")
    with pytest.raises(SafetyError, match="expected long format"):
        local_checks(demo_story(), settings, [], expected_format="long")


@pytest.mark.parametrize("field", ["originality_risk", "policy_risk"])
def test_approved_review_with_elevated_risk_is_rejected(field):
    settings = load_settings(ROOT / "config.yaml")
    review = demo_review()
    setattr(review, field, "medium")
    with pytest.raises(SafetyError, match="risk is medium"):
        review_checks(review, settings)
