"""Synthetic test strings and mocked speech only; never call a provider."""
import base64
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from elsewhere.config import Settings, load_settings
from elsewhere.costs import CostLedger
from elsewhere.demo import demo_story
from elsewhere.models import PublicFigureStoryResponse, StoryDraftResponse
from elsewhere.openai_service import AIService
from elsewhere.performance import instructions, make_plan
from elsewhere.prompts import story_prompt, system_prompt
from elsewhere.recurring_cast import (
    CAST,
    DISCLOSURE,
    FLAGS,
    STYLE,
    VISUAL_IDENTITIES,
    local_cast_errors,
    with_disclosure,
)
from elsewhere.rubrics import RUBRICS

ROOT = Path(__file__).resolve().parents[1]


def settings(category="Funny"):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    raw["dashboard_brief"] = {"selected_category": category}
    return Settings(ROOT, raw)


@pytest.mark.parametrize("category", list(RUBRICS))
def test_every_category_uses_only_safe_recurring_cast(category):
    config = settings(category)
    prompt = system_prompt(config) + story_prompt(config, "short", [])
    assert all(name in prompt for name in CAST)
    assert DISCLOSURE not in prompt and STYLE in prompt
    assert "No other named or speaking character" in prompt
    assert "No endorsements" in prompt and "No real club, team, kit" in prompt
    assert "No sexual or hateful content" in prompt
    assert "No dangerous allegations" in prompt
    assert "never clone, impersonate or imitate" in prompt
    assert "Never imitate or mention a franchise, celebrity, living person" not in prompt


def test_disclosure_is_exact_idempotent_and_does_not_change_script():
    original = demo_story()
    original.description = "x" * 1000
    updated = with_disclosure(original)
    assert updated.youtube_disclosure == DISCLOSURE
    assert updated.description == original.description
    assert len(updated.description) <= 1000
    assert with_disclosure(updated) == updated
    assert original.narration == updated.narration
    assert DISCLOSURE not in original.description
    assert all(FLAGS.values()) and len(FLAGS) == 4


@pytest.mark.parametrize("line", ["Messi and Ronaldo endorse this investment.",
    "Messi and Ronaldo wear Nike.", "Messi and Ronaldo told a true story.",
    "Messi and Ronaldo met Alice. Alice said hello.", "Messi and Ronaldo saw a murder."])
def test_clear_cast_contract_violations_are_blocked(line):
    story = demo_story()
    for scene in story.scenes:
        scene.narration = line
    story.narration = " ".join(s.narration for s in story.scenes)
    assert local_cast_errors(story)


def test_a_single_roster_member_is_a_valid_one_person_story():
    story = demo_story()
    story.narration = "Only Messi is here. Messi looked around the quiet room."
    assert local_cast_errors(story) == []


def test_more_than_two_roster_members_in_one_story_is_blocked():
    story = demo_story()
    story.narration = "Messi, Ronaldo, IShowSpeed and MrBeast all found the box together."
    errors = local_cast_errors(story)
    assert any("at most two" in error for error in errors)


def test_new_story_schema_has_exact_allowed_cast_names():
    from openai.lib._pydantic import to_strict_json_schema
    schema = to_strict_json_schema(PublicFigureStoryResponse)
    assert schema["properties"]["named_characters"]["items"]["enum"] == list(CAST)
    assert schema["additionalProperties"] is False
    assert "named_characters" not in StoryDraftResponse.model_fields  # Legacy responses stay readable.
    with pytest.raises(ValueError):
        PublicFigureStoryResponse.one_or_two_unique_roster_members(["Lionel Messi", "Lionel Messi"])


@pytest.mark.parametrize("line", [
    "Messi found the box. Cristiano said it was a gift.",
    "Lionel asked for paint. Ronaldo replied with a smile.",
])
def test_cast_first_names_do_not_block_approved_story(line):
    story = demo_story()
    story.narration = line
    assert local_cast_errors(story) == []


@pytest.mark.parametrize("line", [
    'Messi and Ronaldo found the card. It said, "Bake this for whoever needs a warm meal."',
    "Messi and Ronaldo opened the box. It whispered nothing, but the note explained everything.",
    'Messi and Ronaldo read the sign. They said the shop was closing soon.',
    'Messi and Ronaldo listened. She said it was a lovely day.',
])
def test_pronouns_are_never_mistaken_for_another_speaking_character(line):
    story = demo_story()
    story.narration = line
    assert local_cast_errors(story) == []


def test_a_genuine_extra_named_character_is_still_blocked():
    story = demo_story()
    story.narration = "Messi and Ronaldo met Priya. Priya said hello."
    assert any("Priya" in error for error in local_cast_errors(story))


def test_performance_plan_keeps_text_and_maps_dialogue_emotions():
    text = 'Messi shouted, “Hurry!” Ronaldo whispered, “A secret.” Messi said, “I am sorry.” They ran home.'
    plan = make_plan(text, settings("Thriller"))
    lines = plan["sentences"]
    assert " ".join(s["sentence_text"] for s in lines) == text
    assert lines[0]["speaker"] == "Lionel Messi" and "shout" in lines[0]["emotion"]
    assert lines[1]["speaker"] == "Cristiano Ronaldo" and "whisper" in lines[1]["emotion"]
    assert lines[2]["intensity"] == "soft"
    assert "emphasize final sentence" in lines[-1]["emotion"]
    assert lines[-2]["pause_after_sentence"] == .5
    for line in lines:
        assert set(line) == {"sentence_text", "speaker", "emotion", "intensity", "speed", "pause_after_sentence"}
        assert .9 <= line["speed"] <= 1.12
    assert plan["speech_requests"] == 1 and plan["voice_imitation"] is False


def test_one_tts_request_has_selected_voice_saved_plan_and_no_imitation(tmp_path):
    config = settings()
    config.raw["models"]["voice"] = "marin"
    calls = []
    destination = tmp_path / "narration-source.wav"
    text = 'Messi said, “What a day!” Ronaldo laughed. They went home.'

    def create(**kwargs):
        calls.append(kwargs)
        assert (tmp_path / "narration-performance-plan.json").exists()
        return SimpleNamespace(parse=lambda: SimpleNamespace(write_to_file=lambda p: p.write_bytes(b"MOCK AUDIO")))

    service = object.__new__(AIService)
    service.settings = config
    service.ledger = CostLedger(tmp_path / "cost-report.json", .5, config.costs)
    service.client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))))
    service.create_speech(text, destination)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-4o-mini-tts" and calls[0]["voice"] == "marin"
    assert calls[0]["input"] == text and calls[0]["response_format"] == "wav"
    assert "Never imitate Lionel Messi, Cristiano Ronaldo, IShowSpeed or MrBeast" in calls[0]["instructions"]
    assert "They went home." in calls[0]["instructions"]
    assert "no clipping" in calls[0]["instructions"]
    assert json.loads((tmp_path / "narration-performance-plan.json").read_text())["voice"] == "marin"
    assert destination.read_bytes() == b"MOCK AUDIO"
    assert "Create" not in instructions(make_plan(text, config), "Read naturally.")


def test_final_image_request_allows_cast_and_repeats_full_identity_contract(tmp_path):
    config = settings()
    calls = []
    data = io.BytesIO()
    Image.new("RGB", (1024, 1536), "navy").save(data, format="PNG")

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data.getvalue()).decode())])

    service = object.__new__(AIService)
    service.settings = config
    service.ledger = CostLedger(tmp_path / "cost-report.json", .5, config.costs)
    service.client = SimpleNamespace(images=SimpleNamespace(generate=generate))
    service.create_image("Mock scene; plain clothing and an ordinary room.", tmp_path / "scene-01.png", "short", 1)
    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert VISUAL_IDENTITIES in prompt
    assert "other public figures" in prompt and "elements, famous people" not in prompt
    assert "No real club, team, kit" in prompt
    assert "polished cinematic slightly stylized illustration" in prompt
