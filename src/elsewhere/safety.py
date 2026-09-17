from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

from .config import Settings
from .disclosure import require_story_content
from .models import EditorialScores, StoryPackage, StoryReview
from .readability import measure
from .recurring_cast import enabled, local_cast_errors
from .rubrics import (
    RUBRICS,
    estimated_seconds,
    general_audience,
    local_category_check,
    selected_category,
)


class SafetyError(ValueError):
    pass


def normalized(text: str) -> str:
    return " ".join("".join(c if c.isalnum() or c.isspace() or unicodedata.category(c).startswith("M")
                            else " " for c in unicodedata.normalize("NFKC", text).casefold()).split())


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalized(left), normalized(right)).ratio()


def local_checks(
    story: StoryPackage,
    settings: Settings,
    prior_ideas: list[str],
    expected_format: str | None = None,
    *,
    enforce_editorial: bool = True,
    advisory_editorial: bool = False,
) -> None:
    if expected_format is not None and story.format != expected_format:
        raise SafetyError(f"expected {expected_format} format, got {story.format}")
    spec = settings.formats[story.format]
    errors: list[str] = []
    try:
        require_story_content(story)
    except ValueError as error:
        errors.append(str(error))
    if enforce_editorial and enabled(settings):
        errors.extend(local_cast_errors(story))
    if len(story.scenes) != int(spec["scene_count"]):
        errors.append(f"expected {spec['scene_count']} scenes, got {len(story.scenes)}")
    if enforce_editorial and not advisory_editorial and not int(spec["narration_words_min"]) <= story.word_count <= int(spec["narration_words_max"]):
        errors.append(
            f"narration has {story.word_count} words, expected "
            f"{spec['narration_words_min']}..{spec['narration_words_max']}"
        )
    if enforce_editorial and not advisory_editorial:
        if story.format == "short":
            if not 115 <= story.word_count <= 135:
                errors.append("Short narration must contain 115..135 words")
            if estimated_seconds(story.word_count) > 60:
                errors.append("estimated narration duration exceeds 60 seconds at 135 words/minute")
        if story.story_category and story.story_category != selected_category(settings):
            errors.append("story category does not match the selected category")
        errors.extend(local_category_check(story.narration, selected_category(settings))["errors"])
        if story.format != "short":
            readability = measure(story.narration, familiar_names=True)
            limits = settings.safety["readability"]
            if readability.flesch_reading_ease < limits["flesch_reading_ease_min"]:
                errors.append("Reading Ease is below the configured limit")
            if readability.flesch_kincaid_grade > limits["flesch_kincaid_grade_max"]:
                errors.append("Grade level exceeds the configured limit")
            if readability.average_sentence_words > limits["average_sentence_words_max"]:
                errors.append("Average sentence length exceeds the configured limit")
    corpus = f"{story.title} {story.premise} {story.narration}".lower()
    if normalized(story.hook) != normalized(story.scenes[0].narration):
        errors.append("hook must exactly match the first scene narration")
    if len({normalized(scene.narration) for scene in story.scenes}) != len(story.scenes):
        errors.append("scene narration must be distinct")
    for term in settings.safety.get("blocked_terms", []):
        if term.lower() in corpus:
            errors.append(f"blocked reference detected: {term}")
    threshold = float(settings.safety["similarity_threshold"])
    for previous in prior_ideas:
        score = similarity(f"{story.title} {story.premise}", previous)
        if score >= threshold:
            errors.append(f"too similar to prior idea ({score:.2f})")
            break
    if errors:
        raise SafetyError("; ".join(errors))


def review_checks(
    review: StoryReview, settings: Settings, *, enforce_editorial: bool = True
) -> None:
    errors: list[str] = []
    if not review.approved:
        errors.append("review approved field is false")
    minimum = max(8, int(settings.safety["minimum_review_score"]))
    if review.score < minimum:
        errors.append(f"review score {review.score} is below {minimum}")
    if review.originality_risk != "low":
        errors.append(f"originality risk is {review.originality_risk}")
    if review.policy_risk != "low":
        errors.append(f"policy risk is {review.policy_risk}")
    if review.problems:
        errors.append("review listed problems: " + "; ".join(review.problems))
    if enforce_editorial:
        if not isinstance(review.category_scores, EditorialScores):
            errors.append("new approvals require all five editorial category scores")
        else:
            minimum_category = max(8, int(settings.safety["minimum_category_score"]))
            for category, score in review.category_scores.model_dump().items():
                if score < minimum_category:
                    errors.append(f"{category} score {score} is below {minimum_category}")
        category = selected_category(settings)
        if not general_audience(settings):
            if review.reviewed_category != category:
                errors.append("review must use the selected category's rubric")
            required = set(RUBRICS[category][1])
            names = [item.name for item in review.category_specific_scores]
            if set(names) != required or len(names) != len(required):
                errors.append("review must supply exactly the relevant category-specific scores")
        for item in review.category_specific_scores:
            if item.score < max(8, int(settings.safety["minimum_category_score"])):
                errors.append(f"{item.name} score {item.score} is below 8")
        if not review.evidence.strip():
            errors.append("review must cite evidence for global quality and the selected category criteria")
        if review.editorial_problems:
            errors.append("editorial rejection: " + "; ".join(review.editorial_problems))
    if errors:
        raise SafetyError("; ".join(errors))
