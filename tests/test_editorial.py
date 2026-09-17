import json
from pathlib import Path

import pytest

from elsewhere.config import load_settings
from elsewhere.demo import demo_review, demo_story
from elsewhere.models import EditorialScores, StoryReview
from elsewhere.readability import measure, syllables
from elsewhere.safety import SafetyError, local_checks, review_checks

ROOT = Path(__file__).resolve().parents[1]


def test_readability_known_one_syllable_sentence():
    value = measure('The cat sat on the mat.')
    assert (value.words, value.sentences, value.syllables) == (6, 1, 6)
    assert value.flesch_reading_ease == pytest.approx(116.145)
    assert value.flesch_kincaid_grade == pytest.approx(-1.45)


@pytest.mark.parametrize(('word', 'expected'), [('erased', 2), ('grabbed', 1), ('photo', 2), ('wife', 1), ('people', 2)])
def test_syllable_edge_cases(word, expected):
    assert syllables(word) == expected


def test_quotes_contractions_and_trailing_sentence():
    value = measure('"Who are you?" I can’t say. She left')
    assert value.words == 8
    assert value.sentences == 3


def test_winner_passes_readability_and_word_bounds():
    candidate = json.loads((ROOT / 'tests/fixtures/editorial_candidates.json').read_text())[0]
    value = measure(candidate['script'])
    assert 115 <= value.words <= 135
    assert value.flesch_reading_ease >= 75
    assert value.flesch_kincaid_grade <= 7
    assert value.average_sentence_words <= 12


@pytest.mark.parametrize('category', list(EditorialScores.model_fields))
def test_any_low_category_rejects_otherwise_approved_review(category):
    scores = dict.fromkeys(EditorialScores.model_fields, 9)
    scores[category] = 7
    review = demo_review()
    review.category_scores = EditorialScores(**scores)
    review.evidence = 'Opening anomaly; personal loss; three events; late reversal of torn photo.'
    with pytest.raises(SafetyError, match=category):
        review_checks(review, load_settings(ROOT / 'config.yaml'))


def test_legacy_review_loads_but_cannot_approve_new_story():
    old = demo_review().model_dump(exclude={'category_scores', 'evidence', 'editorial_problems'})
    review = StoryReview.model_validate(old)
    settings = load_settings(ROOT / 'config.yaml')
    with pytest.raises(SafetyError, match='five editorial'):
        review_checks(review, settings)
    review_checks(review, settings, enforce_editorial=False)


def test_readability_gate_rejects_long_sentences_before_assets():
    story = demo_story()
    for scene in story.scenes:
        scene.narration = ' '.join(['the'] * 15) + '.'
    story.narration = " ".join(s.narration for s in story.scenes)
    story.hook = story.scenes[0].narration
    with pytest.raises(SafetyError, match='average sentence length'):
        local_checks(story, load_settings(ROOT / 'config.yaml'), [])


def test_high_scores_do_not_override_editorial_failure():
    review = demo_review()
    review.category_scores = EditorialScores(**dict.fromkeys(EditorialScores.model_fields, 9))
    review.evidence = 'The ending repairs a pipe.'
    review.editorial_problems = ['Ending only solves a technical problem.']
    with pytest.raises(SafetyError, match='technical problem'):
        review_checks(review, load_settings(ROOT / 'config.yaml'))
