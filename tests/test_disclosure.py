"""Regression tests use the saved lighthouse text and fake provider responses only."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import to_strict_json_schema

from elsewhere.captions import caption_records, scene_durations
from elsewhere.config import Settings, load_settings
from elsewhere.costs import CostLedger
from elsewhere.disclosure import DISCLOSURE, require_story_content, youtube_description
from elsewhere.easy_english import analyze
from elsewhere.models import (
    PublicFigureStoryResponse,
    Scene,
    StoryDraftResponse,
    StoryPackage,
    VisualPlan,
)
from elsewhere.openai_service import AIService
from elsewhere.performance import make_plan
from elsewhere.recurring_cast import with_disclosure
from elsewhere.renderer import synthetic_alignment
from elsewhere.rubrics import local_category_check

ROOT = Path(__file__).resolve().parents[1]
LIGHTHOUSE_SCENES = [
    "The old lighthouse bell vanished before sunrise. Lionel Messi found only its round mark.",
    "He was the careful keeper there. Cristiano Ronaldo was the tall repair guide.",
    "Ronaldo spotted wet marks below an open window. They crossed the dusty floor.",
    "The marks stopped beside a locked wooden chest. Messi noticed red paint on its latch.",
    "Messi said, “The bell left this way.” Ronaldo checked his tool bag.",
    "Its cloth flap held fresh red paint. It also held a tiny brass screw.",
    "The screw matched the chest hinge. Ronaldo remembered moving the bell for repairs.",
    "Then he locked it away and lost the key. They opened the chest with a spare hook. The bell rang again, and the mistake was logged.",
]


def lighthouse(contaminated=False):
    payload = json.loads((ROOT / "stories/wedding-photo-test.json").read_text())
    payload.update(title="The Vanished Lighthouse Bell", story_category="Crime",
                   hook=LIGHTHOUSE_SCENES[0], scenes=[{"narration": s,
                   "visual_prompt": "Mock lighthouse scene directions; no generated asset."}
                   for s in [*LIGHTHOUSE_SCENES, *([DISCLOSURE] if contaminated else [])]])
    payload["description"] = "A fictional lighthouse puzzle for entertainment." + (" " + DISCLOSURE if contaminated else "")
    return StoryPackage.model_validate(payload)


def config(tmp_path):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    raw["dashboard_brief"] = {"selected_category": "Crime"}
    return Settings(tmp_path, raw)


def test_fixed_disclosure_is_backend_only_and_all_measurements_use_narration(tmp_path):
    original = lighthouse(True)
    before = original.model_dump_json()
    repaired = with_disclosure(original)
    expected = lighthouse()
    assert original.model_dump_json() == before
    assert repaired.narration == expected.narration == " ".join(LIGHTHOUSE_SCENES)
    assert repaired.word_count == 120 and len(repaired.scenes) == 8
    assert analyze(repaired.narration) == analyze(expected.narration)
    assert analyze(repaired.narration)["estimated_seconds"] == 53.33
    assert analyze(repaired.narration)["passed"]
    assert repaired.youtube_disclosure == DISCLOSURE
    assert DISCLOSURE not in repaired.description
    assert youtube_description(repaired).endswith(DISCLOSURE)
    assert with_disclosure(repaired) == repaired
    require_story_content(repaired)
    plan = make_plan(repaired.narration, config(tmp_path))
    assert " ".join(s["sentence_text"] for s in plan["sentences"]) == repaired.narration
    assert DISCLOSURE not in json.dumps(plan)
    alignment = synthetic_alignment(repaired, 53.33)
    captions = caption_records(repaired.narration, alignment, 6)
    assert " ".join(" ".join(c[2].split()) for c in captions) == repaired.narration
    assert len(scene_durations(repaired, alignment)) == 8
    saved = json.loads(repaired.model_dump_json())
    assert all(k in saved for k in ("title", "narration", "scenes", "youtube_disclosure"))


@pytest.mark.parametrize("schema_type", [StoryDraftResponse, PublicFigureStoryResponse])
def test_model_schema_cannot_return_backend_disclosure(schema_type):
    schema = to_strict_json_schema(schema_type)
    assert "narration" in schema["required"]
    assert "youtube_disclosure" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_story_response_gets_metadata_from_backend_without_another_request(tmp_path):
    story = lighthouse()
    payload = story.model_dump(exclude={"youtube_disclosure"})
    payload.update(creative_fingerprint={"main_object": "bell", "setting": "lighthouse",
                   "characters": "Messi and Ronaldo", "twist": "a misplaced bell"},
                   draft_scores={k: 9 for k in ("simple_language", "opening_hook", "coherence", "category_match", "ending_payoff")},
                   draft_category_scores=[{"name": "investigation", "score": 9}],
                   named_characters=["Lionel Messi", "Cristiano Ronaldo"],
                   speaking_characters=["Lionel Messi"])
    parsed = PublicFigureStoryResponse.model_validate(payload)
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=parsed, model="mock", usage={"input_tokens": 10, "output_tokens": 10})

    service = object.__new__(AIService)
    service.settings = config(tmp_path)
    service.ledger = CostLedger(tmp_path / "cost-report.json", .5, service.settings.costs)
    service.client = SimpleNamespace(responses=SimpleNamespace(parse=generate))
    result = service.create_story("short", [])
    assert len(calls) == 1 and result.narration == story.narration
    assert result.youtube_disclosure == DISCLOSURE
    assert DISCLOSURE not in json.dumps(calls, default=str)
    assert "youtube_disclosure" not in parsed.model_dump()
    assert [r["stage"] for r in service.ledger.requests] == ["story_generation"]


def test_separate_narration_is_authoritative_and_extra_scene_text_is_rejected():
    story = with_disclosure(lighthouse())
    before = analyze(story.narration)
    story.scenes.append(Scene(narration=DISCLOSURE, visual_prompt="Wrong metadata-only scene directions."))
    assert analyze(story.narration) == before and story.word_count == 120
    with pytest.raises(ValueError, match="disclosure"):
        require_story_content(story)


def test_caption_and_performance_entry_points_reject_leaked_metadata(tmp_path):
    story = lighthouse()
    text = story.narration + " " + DISCLOSURE
    with pytest.raises(ValueError, match="disclosure"):
        make_plan(text, config(tmp_path))
    with pytest.raises(ValueError, match="disclosure"):
        caption_records(text, synthetic_alignment(story, 53.33), 6)


def test_tts_and_visual_planning_receive_content_not_description(tmp_path):
    story = with_disclosure(lighthouse(True))
    settings = config(tmp_path)
    service = object.__new__(AIService)
    service.settings = settings
    service.ledger = CostLedger(tmp_path / "cost-report.json", .5, settings.costs)
    calls = []

    def speech(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parse=lambda: SimpleNamespace(write_to_file=lambda p: p.write_bytes(b"MOCK AUDIO")))

    def visual(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=VisualPlan(
            continuity_bible="Mock full lighthouse character and location continuity only.",
            prompts=["Mock lighthouse action and framing for scene " + str(i) for i in range(8)]),
            model="mock", usage={"input_tokens": 10, "output_tokens": 10})

    service.client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=speech))), responses=SimpleNamespace(parse=visual))
    service.create_speech(story.narration, tmp_path / "narration-source.wav")
    plan = service.prepare_image_prompts(story)
    assert calls[0]["input"] == story.narration
    assert DISCLOSURE not in json.dumps(calls, default=str)
    assert "youtube_disclosure" not in calls[1]["input"]
    assert DISCLOSURE not in plan.model_dump_json()
    count = len(service.ledger.requests)
    with pytest.raises(ValueError, match="disclosure"):
        service.create_speech(story.narration + " " + DISCLOSURE, tmp_path / "bad.wav")
    with pytest.raises(ValueError, match="disclosure"):
        service.create_image(DISCLOSURE, tmp_path / "bad.png", "short", 1)
    with pytest.raises(ValueError, match="disclosure"):
        service.prepare_image_prompts(lighthouse(True))
    assert len(service.ledger.requests) == count


@pytest.mark.parametrize("ending", [
    "The bell rang again, and the mistake was logged.",
    "Nobody had stolen it. It was only an accident.",
    "He had simply forgotten where he left it.",
])
def test_accident_alone_cannot_satisfy_crime_even_after_theft_suspicion(ending):
    text = "He thought someone stole the bell. He followed the marks. " + ending
    result = local_category_check(text, "Crime")
    assert result["errors"] and result["score_cap"] == 3
    assert local_category_check(text, "Mystery")["errors"] == []


def test_lighthouse_gets_crime_mismatch_and_intentional_trick_is_distinct():
    result = local_category_check(lighthouse().narration, "Crime")
    assert result["errors"] and "mistake" in " ".join(result["evidence"])
    text = "The bell was gone. Wet marks led to his bag. He hid it on purpose to trick his friend."
    assert not local_category_check(text, "Crime")["errors"]
    assert not local_category_check(text + " It was not an accident.", "Crime")["errors"]
    missing_only = "The bell was gone. They found it in the box."
    assert local_category_check(missing_only, "Crime")["errors"]
