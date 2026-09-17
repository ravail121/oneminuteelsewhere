"""Local language screening uses bundled corpus data; all test sockets are blocked."""
import copy
from pathlib import Path

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.easy_english import LIMITS, analyze, sentence_spans
from elsewhere.prompts import story_prompt, system_prompt
from elsewhere.readability import measure
from elsewhere.rubrics import RUBRICS

ROOT = Path(__file__).resolve().parents[1]


def plain_words(count, sentence_size=8):
    # Test tokens only, not a generated story.
    return " ".join(" ".join(["the"] * min(sentence_size, count - i)) + "."
                    for i in range(0, count, sentence_size))


@pytest.mark.parametrize("category", list(RUBRICS))
def test_every_category_has_very_easy_english_and_keeps_cast(category):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    raw["dashboard_brief"] = {"selected_category": category}
    settings = Settings(ROOT, raw)
    for prompt in (system_prompt(settings), story_prompt(settings, "short", [])):
        for rule in ("VERY EASY ENGLISH", "8-10-year-old", "115-130", ">=85", "<=4",
                     "<=10", "maximum sentence 14", "one", "first listen",
                     "Lionel Messi", "Cristiano Ronaldo", "selected category"):
            assert rule in prompt
        assert "teenagers and adults" in prompt
        assert "active voice" in prompt and "order they happen" in prompt


@pytest.mark.parametrize("count,passed", [(114, False), (115, True), (130, True), (135, True), (136, False)])
def test_word_and_duration_bounds(count, passed):
    result = analyze(plain_words(count))
    assert result["passed"] is passed
    assert result["estimated_seconds"] == round(count / 135 * 60, 2)
    if count == 136:
        assert {i["kind"] for i in result["issues"]} >= {"word_count", "duration"}
    if passed:
        assert result["result"] == "Easy enough for children"


def test_strict_readability_targets_and_sentence_highlights():
    assert LIMITS == {"words_min": 115, "generation_words_max": 130, "words_max": 135,
                      "flesch_reading_ease_min": 85, "flesch_kincaid_grade_max": 4,
                      "average_sentence_words_max": 10, "maximum_sentence_words": 14,
                      "estimated_seconds_max": 60}
    long_sentence = " ".join(["the"] * 15) + "."
    text = long_sentence + " " + plain_words(105)
    result = analyze(text)
    assert result["metrics"]["average_sentence_words"] < 10
    problem = next(i for i in result["issues"] if i["kind"] == "sentence_length")
    assert problem["text"] == long_sentence
    assert text[problem["start"]:problem["end"]] == long_sentence
    assert "maximum 14" in problem["message"]
    assert not any(i["kind"] == "sentence_length" for i in analyze(plain_words(120, 14))["issues"])
    assert any(i["kind"] == "average_sentence_words" for i in analyze(plain_words(120, 12))["issues"])
    hard = analyze(plain_words(120).replace("the", "happy"))
    assert {i["kind"] for i in hard["issues"]} >= {"flesch_reading_ease", "flesch_kincaid_grade"}


def test_names_and_possessives_do_not_increase_difficulty():
    text = "Lionel Messi and Cristiano Ronaldo took Messi’s ball from Ronaldo's bag."
    name_adjusted = measure(text, familiar_names=True)
    assert name_adjusted.syllables == name_adjusted.words
    assert name_adjusted.words == measure(text).words
    assert not any(i["kind"] == "word_familiarity" for i in analyze(text)["issues"])


def test_corpus_familiarity_uses_nearby_explanation_not_fixed_bans():
    unfamiliar = analyze("Messi saw a nebula.")
    explained = analyze("Messi saw a nebula, a cloud of dust in space.")
    issue = next(i for i in unfamiliar["issues"] if i["kind"] == "word_familiarity")
    assert issue["text"] == "nebula" and 0 < issue["frequency"] < 4.5
    assert not any(i["kind"] == "word_familiarity" for i in explained["issues"])
    everyday = analyze("Messi held a kitten. Ronaldo ate a pancake.")
    assert not any(i["kind"] == "word_familiarity" for i in everyday["issues"])


def test_complex_structure_passive_and_exact_spans():
    text = '  “Ronaldo was pushed by Messi.”\nHe left because rain fell while Messi slept.  '
    result = analyze(text)
    assert {i["kind"] for i in result["issues"]} >= {"passive_voice", "sentence_structure"}
    assert "".join(s["text"] for s in result["segments"]) == text
    for issue in result["issues"]:
        if issue["start"] is not None:
            assert text[issue["start"]:issue["end"]] == issue["text"]
    assert len(list(sentence_spans('“Who are you?” I can’t say. She left'))) == 3


@pytest.mark.parametrize("text", ["", "!!!", "照片"])
def test_unmeasurable_text_returns_problems_not_dashboard_error(text):
    result = analyze(text)
    assert not result["passed"] and result["metrics"]["words"] == 0
    assert any(i["kind"] == "missing_story" for i in result["issues"])
    assert "".join(s["text"] for s in result["segments"]) == text
