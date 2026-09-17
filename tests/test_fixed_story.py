import json
from pathlib import Path

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.models import EditorialScores, StoryPackage, StoryReview
from elsewhere.pipeline import Pipeline
from elsewhere.rubrics import DEFAULT_CATEGORY, RUBRICS
from elsewhere.safety import SafetyError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "stories/wedding-photo-test.json"
EXACT = '''Our wedding photo erased me. My wife was still there, but I was gone.
She stared at me. “Who are you?”
I showed her my ring. She backed away and reached for her phone.
I grabbed the frame by its torn edge. My face suddenly returned.
She lowered the phone. “What’s happening?”
Before I could answer, my face began fading again.
I held the frame tightly, too scared to blink.
My wife screamed. My hands disappeared. Then my voice did too.
She reached for me, but her fingers struck cold glass.
I was trapped inside the photo.
Outside, another man put his arm around my wife.
I knew his face.
Last night, I had cut him out of that same wedding photo.'''


def test_supplied_script_is_verbatim_and_has_eight_consistent_prompts():
    story = StoryPackage.model_validate_json(SOURCE.read_text())
    assert story.narration == " ".join(EXACT.split())
    assert story.title == "The Wedding Photo Erased Me"
    assert len(story.scenes) == 8
    bibles = [scene.visual_prompt.split("SCENE ")[0] for scene in story.scenes]
    assert len(set(bibles)) == 1


@pytest.mark.parametrize("approved", [True, False])
def test_exact_story_is_reviewed_once_and_never_replaced(tmp_path, monkeypatch, approved):
    calls = []

    class FakeService:
        def __init__(self, settings, ledger):
            pass

        def verify_model_access(self):
            return []

        def review_story(self, story):
            calls.append(story.narration)
            return StoryReview(
                approved=approved, score=9 if approved else 7,
                policy_risk="low", originality_risk="low",
                category_scores=EditorialScores(**dict.fromkeys(EditorialScores.model_fields, 9)),
                reviewed_category=DEFAULT_CATEGORY,
                category_specific_scores=[{"name": k, "score": 9} for k in RUBRICS[DEFAULT_CATEGORY][1]],
                evidence="A test-only review; never sent to production.",
            )

        def create_story(self, *args):
            pytest.fail("Exact script must never trigger story generation")

        def create_image(self, *args):
            pytest.fail("Review-only must not generate media")

    monkeypatch.setattr("elsewhere.pipeline.AIService", FakeService)
    settings = Settings(root=tmp_path, raw=load_settings(ROOT / "config.wedding-test.yaml").raw)
    pipeline = Pipeline(settings)
    kwargs = {"format_name": "short", "paid_approved": True, "allowance_usd": 0.50, "review_only": True}
    if approved:
        pipeline.run(story_file=SOURCE, **kwargs)
    else:
        with pytest.raises(SafetyError):
            pipeline.run(story_file=SOURCE, **kwargs)
    job = next((tmp_path / "output").iterdir())
    if approved:
        pipeline.run(resume=job, **kwargs)
    else:
        with pytest.raises(SafetyError):
            pipeline.run(resume=job, **kwargs)
    assert len(calls) == 1
    assert not list(job.glob("scene-*.png"))
    assert json.loads((job / "run-state.json").read_text())["fixed_story"]
