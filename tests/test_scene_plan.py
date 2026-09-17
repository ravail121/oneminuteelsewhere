from elsewhere.easy_english import analyze
from elsewhere.models import Scene
from elsewhere.scene_plan import narration_scenes, repair_scene_partition


def test_introductory_comma_and_simple_list_are_not_complex_clauses():
    result = analyze("Soon, it rolled past flour, sugar, and chairs.")
    assert not any(i["kind"] == "sentence_structure" for i in result["issues"])
    complex_text = "He waited because it was dark while she looked outside."
    assert any(i["kind"] == "sentence_structure" for i in analyze(complex_text)["issues"])


def test_scene_repair_keeps_authoritative_words_and_discards_stale_directions():
    from elsewhere.demo import demo_story
    story = demo_story()
    text = story.narration
    story.scenes = [Scene(narration="Only an incomplete fragment remains.", visual_prompt="Stale directions from the broken response.")]
    repaired = repair_scene_partition(story)
    assert repaired.narration == text and repaired.title == story.title
    assert len(repaired.scenes) == 8
    assert " ".join(s.narration for s in repaired.scenes).split() == text.split()
    assert all("Stale" not in s.visual_prompt for s in repaired.scenes)
    assert repair_scene_partition(repaired) == repaired
    assert len(story.scenes) == 1


def test_short_sentences_partition_without_empty_scenes():
    text = " ".join(["The bell rang."] * 40)
    scenes = narration_scenes(text)
    assert len(scenes) == 8
    assert " ".join(s.narration for s in scenes) == text
