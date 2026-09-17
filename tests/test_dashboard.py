"""Offline browser/workflow tests. The real Pipeline runs only against mocked services/media."""
import copy
import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from elsewhere.config import Settings, load_settings
from elsewhere.costs import BudgetExceeded, CostLedger
from elsewhere.dashboard import create_app
from elsewhere.dashboard_store import Conflict, DashboardStore, NewVideo
from elsewhere.models import (
    CategoryScore,
    EditorialScores,
    Scene,
    StoryPackage,
    StoryReview,
    VisualPlan,
)
from elsewhere.openai_service import AIService, save_json
from elsewhere.renderer import synthetic_alignment
from elsewhere.rubrics import RUBRICS, selected_category

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    state = SimpleNamespace(calls=[], reject=False, duplicate=False, fail_render=False, cancel_after=None,
                            ambiguous=False, manager=None, project_id=None, generation=0,
                            planned_stories=[], reviewed_stories=[], image_prompts=[])
    seed = StoryPackage.model_validate_json((ROOT / "stories/wedding-photo-test.json").read_text())
    seed.continuity_bible = "Fictional adult characters, stable everyday clothing and a consistent ordinary room."
    seed.creative_fingerprint = {"main_object": "wedding photograph", "setting": "living room",
                                "characters": "married couple", "twist": "revenge by an erased rival"}
    seed.draft_scores = dict.fromkeys(EditorialScores.model_fields, 9)

    class FakeService:
        def __init__(self, settings, ledger):
            self.ledger = ledger
            self.category = selected_category(settings)

        def verify_model_access(self):
            return [{"requested": "mock", "returned": "mock", "request_id": "mock"}]

        def paid(self, stage, cost=.01):
            def response():
                state.calls.append(stage)
                if state.ambiguous:
                    raise TimeoutError("private SDK payload must not be exposed")
                return SimpleNamespace(model="mock", _request_id="mock-request", usage={"input_tokens": 10})
            return self.ledger.paid_call(stage=stage, model="mock", reserve_usd=.03, call=response,
                usage_getter=lambda r: r.usage, cost_calculator=lambda _: (cost, "Mock calculation; no real request"))

        def create_story(self, format_name, previous):
            self.paid("story_generation")
            state.generation += 1
            story = seed.model_copy(deep=True)
            # Viral Material has no fixed pre-selected rubric category: a real model would
            # choose its own real-content angle and self-assessed score names for it.
            story.story_category = self.category or "Real Story"
            story.draft_category_scores = ([CategoryScore(name=k, score=9) for k in RUBRICS[self.category][1]]
                if self.category in RUBRICS else
                [CategoryScore(name="accuracy", score=9), CategoryScore(name="clarity", score=9)])
            if state.generation > 1 and not state.duplicate:
                # Existing fixture data, not newly generated content.
                script = json.loads((ROOT / "tests/fixtures/editorial_candidates.json").read_text())[1]["script"]
                words = script.split()
                parts = [" ".join(words[round(i*len(words)/8):round((i+1)*len(words)/8)]) for i in range(8)]
                story.title = "The Spare Key Mock Draft"
                story.scenes = [Scene(narration=t, visual_prompt="Mock visual prompt; never sent to an API") for t in parts]
                story.hook = parts[0]
                story.creative_fingerprint = {"main_object": "metal key", "setting": "city office",
                                             "characters": "shop clerk and stranger", "twist": "memory copied into an android"}
            story.narration = " ".join(s.narration for s in story.scenes)
            return story

        def review_story(self, story):
            pytest.fail("Dashboard must never call an independent reviewer")

        def create_image(self, prompt, destination, format_name, index):
            state.image_prompts.append(prompt)
            self.paid(f"image_{index:02d}", .02)
            Image.new("RGB", (1024, 1536), "navy").save(destination)
            if state.cancel_after == index:
                state.manager.cancel(state.project_id)

        def prepare_image_prompts(self, story):
            state.planned_stories.append(story.model_dump())
            self.paid("image_prompts")
            return VisualPlan(continuity_bible="Mock complete fictional cast and location descriptions; never real API input.",
                              prompts=["Mock cinematic scene action number " + str(i) for i in range(8)])

        def create_speech(self, text, destination, *, pacing_hint=""):
            self.paid("speech_generation")
            destination.write_bytes(b"MOCK AUDIO - not real speech")

        def finalize_speech_cost(self, duration, text):
            pass

        def align_speech(self, path, duration):
            self.paid("caption_alignment", .006)
            story = StoryPackage.model_validate_json((path.parent / "story.json").read_text())
            return synthetic_alignment(story, duration)

    def normalize(source, destination, minimum, maximum):
        destination.write_bytes(source.read_bytes())
        return 55

    def render(story, images, narration, alignment, settings, directory, **kwargs):
        progress = kwargs["progress"]
        progress("captions", "running", "Mock captions")
        progress("captions", "completed", "Mock captions saved")
        progress("rendering", "running", "Mock rendering")
        if state.fail_render:
            raise RuntimeError("Mock local render failure")
        path = directory / "final.mp4"
        path.write_bytes(b"MOCK MP4 - no real video generated")
        progress("rendering", "completed", "Mock MP4 saved")
        return path

    monkeypatch.setattr("elsewhere.pipeline.media_duration", lambda _: 55)
    monkeypatch.setattr("elsewhere.pipeline.normalize_narration", normalize)
    monkeypatch.setattr("elsewhere.pipeline.render_video", render)
    monkeypatch.setattr("elsewhere.pipeline.validate_video", lambda *args: {
        "checks": {"dimensions_1080x1920": True}, "measured": {"duration_seconds": 55,
            "width": 1080, "height": 1920, "frame_rate": "30/1", "video_codec": "h264", "audio_codec": "aac",
            "audio_sample_rate": 48000, "audio_mean_volume_db": -17, "audio_max_volume_db": -1.3}})
    settings = Settings(root=tmp_path, raw=copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    settings.raw.pop("recurring_cast", None)  # Legacy fixture workflow; new-cast behavior has separate tests.
    manager = DashboardStore(settings, service_factory=FakeService, synchronous=True)
    app = create_app(settings, store=manager)
    app.testing = True
    state.manager = manager
    state.settings = settings
    state.app = app
    state.client = app.test_client()
    state.service_factory = FakeService
    return state


def new(studio, limit=.50):
    project_id = studio.manager.create(NewVideo(max_cost=limit), "a" * 64)
    studio.project_id = project_id
    return project_id


def act(studio, action):
    p = studio.manager.load(studio.project_id)
    studio.manager.dispatch(p["id"], action, p["version"])


def test_diagnostic_costs_are_aggregated_without_clearing_uncertainty(studio):
    project_id = new(studio)
    act(studio, "generate")
    directory = studio.manager.revision_dir(studio.manager.load(project_id))
    original = (directory / "cost-report.json").read_bytes()
    diagnostic = directory / "image-diagnostic-0001"
    diagnostic.mkdir()
    save_json(diagnostic / "cost-report.json", {"requests": [
        {"stage": "image_01", "status": "failed", "calculated_cost_usd": None,
         "charge_status": "unknown_ambiguous_failure"},
        {"stage": "image_01", "status": "succeeded", "calculated_cost_usd": .04,
         "charge_status": "calculated_from_usage"}]})
    report = studio.manager.report(project_id)
    assert report["calculated_estimate_usd"] == .05
    assert report["unknown_charges"] == 1 and report["blocked_by_uncertainty"]
    assert report["requests"][-1]["diagnostic_attempt"] is True
    assert (directory / "cost-report.json").read_bytes() == original
    with pytest.raises(BudgetExceeded):
        studio.manager.guard(project_id, .01, "image_02")


def test_story_waits_for_manual_approval_and_production_reuses_pipeline(studio):
    project_id = new(studio)
    act(studio, "generate")
    assert studio.calls == ["story_generation"]
    p = studio.manager.public(project_id)
    assert p["status"] == "story_ready" and p["story"]["title"]
    assert not p["images"] and not p["evaluation"]["warnings"]
    act(studio, "approve")
    assert studio.manager.public(project_id)["status"] == "complete"
    assert studio.calls == ["story_generation", "image_prompts"] + [f"image_{i:02d}" for i in range(1, 9)] + ["speech_generation", "caption_alignment"]
    assert studio.manager.public(project_id)["progress"] == 100


def test_incomplete_generated_scenes_are_repaired_for_one_click_production(studio, monkeypatch):
    import re
    original = studio.service_factory.create_story
    texts = []

    def incomplete(service, *args):
        story = original(service, *args)
        texts.append(story.narration)
        story.scenes = story.scenes[:1]
        return story

    monkeypatch.setattr(studio.service_factory, "create_story", incomplete)
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.public(project_id)
    assert p["story"]["narration"] == texts[0]
    assert p["evaluation"]["scene_count"] == 8 and not p["evaluation"]["errors"]
    page = studio.client.get(f"/projects/{project_id}", base_url=BASE).data.decode()
    button = re.search(r"<button([^>]*)>Create Video From This Story</button>", page)
    assert button and "disabled" not in button[1]
    assert studio.calls == ["story_generation"]
    act(studio, "approve")
    assert studio.manager.public(project_id)["status"] == "complete"
    assert studio.calls.count("story_generation") == 1 and "quality_review" not in studio.calls
    assert studio.planned_stories[0]["narration"] == texts[0]


def test_historical_rejected_review_does_not_block_final_human_approval(studio):
    new(studio)
    act(studio, "generate")
    p = studio.manager.load(studio.project_id)
    directory = studio.manager.revision_dir(p)
    review = StoryReview(approved=False, score=2, originality_risk="high", policy_risk="medium",
                         evidence="Historical mock rejection; not a current assessment.")
    save_json(directory / "review.json", review)
    # Recreate a stopped review-era run with exact inputs/configuration and cost.
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    save_json(directory / "story.json", story)
    save_json(directory / "supplied-story.json", story)
    save_json(directory / "settings-snapshot.json", studio.manager.settings(p).raw)
    save_json(directory / "human-approval.json", {"approved_at": "historical", "revision": 1})
    save_json(directory / "run-state.json", {"format": "short", "dry_run": False,
              "fixed_story": True, "content_id": None, "status": "failed"})
    ledger_path = directory / "cost-report.json"
    ledger_data = json.loads(ledger_path.read_text())
    old_charge = {"stage": "quality_review", "status": "succeeded", "calculated_cost_usd": .02,
                  "charge_status": "calculated_from_usage", "model_requested": "mock", "usage": {"input_tokens": 10}}
    ledger_data["requests"].append(old_charge)
    save_json(ledger_path, ledger_data)
    before = (directory / "review.json").read_bytes()
    p["status"] = "review_rejected"
    studio.manager.save(p)
    assert studio.manager.public(p["id"])["status"] == "story_ready"
    act(studio, "approve")
    assert "quality_review" not in studio.calls and studio.calls.count("story_generation") == 1
    assert studio.manager.public(studio.project_id)["status"] == "complete"
    assert (directory / "review.json").read_bytes() == before
    assert old_charge in json.loads(ledger_path.read_text())["requests"]
    assert list((directory / "approval-history").glob("*.json"))
    assert (directory / "story.json").read_text() == (directory / "supplied-story.json").read_text()


def test_regenerate_preserves_story_history_and_accumulates_cost(studio):
    new(studio)
    act(studio, "generate")
    first = studio.manager.revision_dir(studio.manager.load(studio.project_id)) / "draft.json"
    content = first.read_bytes()
    act(studio, "generate")
    p = studio.manager.public(studio.project_id)
    assert first.read_bytes() == content
    assert len(p["drafts"]) == 2 and p["calculated_cost"] == .02
    assert p["story"]["title"] != json.loads(content)["title"]
    assert studio.calls == ["story_generation", "story_generation"]
    assert not p["evaluation"]["warnings"]


def test_similarity_is_advisory_and_does_not_override_final_approval(studio):
    new(studio)
    act(studio, "generate")
    studio.duplicate = True
    act(studio, "generate")
    assert "Too similar" in " ".join(studio.manager.public(studio.project_id)["evaluation"]["warnings"])
    assert len(studio.calls) == 2
    act(studio, "approve")
    assert studio.manager.public(studio.project_id)["status"] == "complete"
    assert studio.calls.count("story_generation") == 2 and "quality_review" not in studio.calls


def test_edits_are_free_preserve_history_and_clear_scores(studio):
    new(studio)
    act(studio, "generate")
    p = studio.manager.public(studio.project_id)
    script = " ".join(s["narration"] for s in p["story"]["scenes"])
    studio.manager.edit(p["id"], p["version"], "An Edited Wedding Photo", script)
    edited = studio.manager.public(p["id"])
    assert len(edited["drafts"]) == 2 and edited["evaluation"]["draft_scores"] == {}
    assert edited["evaluation"]["readability"]["words"] == 122
    assert studio.calls == ["story_generation"]


def test_failed_language_draft_is_saved_highlighted_and_recovers_without_requests(studio, monkeypatch):
    original_create = studio.service_factory.create_story

    def difficult_draft(service, format_name, previous):
        story = original_create(service, format_name, previous)
        for scene in story.scenes:
            scene.narration = scene.narration.replace("ring", "anachronism")
        story.narration = " ".join(s.narration for s in story.scenes)
        return story

    monkeypatch.setattr(studio.service_factory, "create_story", difficult_draft)
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.public(project_id)
    report = p["evaluation"]["very_easy_english"]
    assert p["status"] == "story_ready" and not p["evaluation"]["errors"]
    assert not report["passed"] and any(i["text"] == "anachronism" for i in report["issues"])
    directory = studio.manager.revision_dir(studio.manager.load(project_id))
    draft_bytes = (directory / "draft.json").read_bytes()
    assert json.loads((directory / "draft-evaluation.json").read_text())["very_easy_english"] == report
    import re
    initial_page = studio.client.get(f"/projects/{project_id}", base_url=BASE).data.decode()
    button = re.search(r'<button[^>]*>Create Video From This Story</button>', initial_page)
    assert button and "disabled" not in button.group()
    for _ in range(2):
        response = studio.client.get(f"/projects/{project_id}", base_url=BASE)
        assert response.status_code == 200
        assert b'class="language-highlight"' in response.data
        assert b"anachronism" in response.data and b"Simpler wording" in response.data
        assert b"nothing is regenerated automatically" in response.data
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    assert restored.public(project_id)["evaluation"]["very_easy_english"] == report
    assert (directory / "draft.json").read_bytes() == draft_bytes
    assert studio.calls == ["story_generation"] and p["calculated_cost"] == .01

    fixture = StoryPackage.model_validate_json((ROOT / "stories/wedding-photo-test.json").read_text())
    restored.edit(project_id, restored.load(project_id)["version"], fixture.title, fixture.narration)
    repaired = restored.public(project_id)
    assert repaired["evaluation"]["very_easy_english"]["passed"]
    assert not repaired["evaluation"]["errors"] and len(repaired["drafts"]) == 2
    assert studio.calls == ["story_generation"] and repaired["calculated_cost"] == .01
    assert (directory / "draft.json").read_bytes() == draft_bytes
    page = create_app(studio.settings, store=restored).test_client().get(f"/projects/{project_id}", base_url=BASE)
    assert b"Easy enough for children" in page.data and b'<mark class="language-highlight"' not in page.data


def test_manual_language_failure_has_escaped_highlights_and_no_request(studio):
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.public(project_id)
    script = " ".join(s["narration"] for s in p["story"]["scenes"])
    script = script.replace("ring", '<script>alert("anachronism")</script>')
    studio.manager.edit(project_id, p["version"], "Mock edited language draft", script)
    edited = studio.manager.public(project_id)
    assert not edited["evaluation"]["errors"]
    page = studio.client.get(f"/projects/{project_id}", base_url=BASE)
    assert page.status_code == 200
    assert b'<script>alert(' not in page.data and b"&lt;script&gt;" in page.data
    assert b'class="language-highlight"' in page.data
    assert studio.calls == ["story_generation"]


def test_local_disclosure_repair_preserves_request_and_cost_and_shows_crime_mismatch(studio):
    from test_disclosure import lighthouse

    from elsewhere.recurring_cast import with_disclosure
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.load(project_id)
    p["selected_category"] = "Crime"
    p["options"]["story_type"] = "Crime"
    studio.manager.save(p)
    directory = studio.manager.revision_dir(p)
    old = lighthouse(True)
    old.draft_scores = dict.fromkeys(EditorialScores.model_fields, 9)
    old.draft_category_scores = [CategoryScore(name=k, score=9) for k in RUBRICS["Crime"][1]]
    raw = directory / "api-responses" / "mock-original-response.json"
    raw.parent.mkdir(exist_ok=True)
    save_json(raw, old)
    before = {path: path.read_bytes() for path in (raw, directory / "cost-report.json", directory.parent / "cost-report.json")}
    repaired = with_disclosure(old)
    save_json(directory / "draft.json", repaired)
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    result = restored.public(project_id)
    assert result["evaluation"]["scene_count"] == 8
    assert result["evaluation"]["readability"]["words"] == 120
    assert result["evaluation"]["draft_scores"]["category_match"] == 3
    assert result["evaluation"]["draft_category_scores"]["investigation"] == 3
    page = create_app(studio.settings, store=restored).test_client().get(f"/projects/{project_id}", base_url=BASE)
    assert page.status_code == 200
    assert b"Local category mismatch" in page.data and b"never spoken" in page.data
    assert all(path.read_bytes() == content for path, content in before.items())
    assert studio.calls == ["story_generation"]


def test_duplicate_submit_and_duplicate_new_project_are_blocked(studio):
    project_id = new(studio)
    assert new(studio) == project_id
    version = studio.manager.load(project_id)["version"]
    act(studio, "generate")
    with pytest.raises(Conflict):
        studio.manager.dispatch(project_id, "generate", version)
    assert studio.calls == ["story_generation"]


def test_budget_is_shared_across_generated_drafts(studio):
    new(studio, .05)
    for _ in range(4):
        act(studio, "generate")
    assert len(studio.calls) == 3  # .03 reserve no longer fits after three .01 requests
    assert studio.manager.public(studio.project_id)["status"] == "failed"
    assert studio.manager.public(studio.project_id)["calculated_cost"] == .03


def test_cancel_finishes_current_asset_then_resume_reuses_it(studio):
    new(studio)
    act(studio, "generate")
    studio.cancel_after = 1
    act(studio, "approve")
    p = studio.manager.public(studio.project_id)
    assert p["status"] == "cancelled" and p["images"] == ["scene-01.png"]
    assert "image_02" not in studio.calls
    studio.cancel_after = None
    act(studio, "resume")
    assert studio.manager.public(studio.project_id)["status"] == "complete"
    assert "quality_review" not in studio.calls and studio.calls.count("image_01") == 1


def test_render_failure_resume_never_repeats_paid_requests(studio):
    new(studio)
    act(studio, "generate")
    studio.fail_render = True
    act(studio, "approve")
    calls = list(studio.calls)
    assert studio.manager.public(studio.project_id)["status"] == "failed"
    studio.fail_render = False
    act(studio, "resume")
    assert studio.calls == calls
    assert studio.manager.public(studio.project_id)["status"] == "complete"


def test_ambiguous_failure_blocks_regeneration_and_resume(studio):
    new(studio)
    studio.ambiguous = True
    act(studio, "generate")
    p = studio.manager.public(studio.project_id)
    assert p["cost_blocked"] and p["requests"][0]["calculated_cost_usd"] is None
    with pytest.raises(BudgetExceeded):
        act(studio, "generate")
    with pytest.raises(BudgetExceeded):
        act(studio, "resume")
    assert studio.calls == ["story_generation"]


def test_restart_recovers_progress_without_any_request(studio):
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.load(project_id)
    p.update(busy=True, status="producing", operation="produce")
    studio.manager.save(p)
    studio.manager.event(project_id, "image_01", "running", "In progress")
    ledger = CostLedger(studio.manager.revision_dir(p) / "cost-report.json", .5, studio.settings.costs)
    ledger.requests.append({"stage": "image_01", "status": "in_progress", "charge_status": "unknown_in_progress", "calculated_cost_usd": None})
    ledger.save()
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    snapshot = restored.public(project_id)
    assert snapshot["status"] == "interrupted" and snapshot["cost_blocked"]
    assert next(s for s in snapshot["stages"] if s["key"] == "image_01")["status"] == "failed"
    assert studio.calls == ["story_generation"]


def browser(studio):
    studio.client.get("/", base_url=BASE)
    with studio.client.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    return csrf


def test_browser_confirmation_security_and_no_startup_requests(studio):
    csrf = browser(studio)
    assert studio.calls == []
    assert studio.client.get("/", base_url="http://evil.example").status_code == 403
    assert studio.client.post("/projects", base_url=BASE, data={}).status_code == 403
    assert studio.client.post("/projects", base_url=BASE, headers={"Origin":"https://evil.example"}, data={"csrf":csrf}).status_code == 403
    response = studio.client.post("/projects", base_url=BASE, data={"csrf":csrf,"submission":"b"*64})
    assert response.status_code == 303 and studio.calls == []
    confirmation = studio.client.get(response.location, base_url=BASE)
    assert b"paid OpenAI API" in confirmation.data and b"gpt-5.6-luna" in confirmation.data
    assert b"Conservative request reserve" in confirmation.data


def test_native_local_form_preserves_origin_and_reaches_confirmation_only(studio):
    csrf = browser(studio)
    page = studio.client.get("/", base_url=BASE)
    assert page.headers["Referrer-Policy"] == "same-origin"
    response = studio.client.post("/projects", base_url=BASE,
        headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"},
        data={"csrf": csrf, "submission": "c" * 64})
    assert response.status_code == 303
    confirmation = studio.client.get(response.location, base_url=BASE)
    assert confirmation.status_code == 200
    assert confirmation.headers["Referrer-Policy"] == "same-origin"
    assert b"Confirm and Generate One Story" in confirmation.data
    assert studio.calls == []


@pytest.mark.parametrize("origin", ["null", "https://evil.example", "http://localhost:8765", "http://127.0.0.1:9999"])
def test_invalid_origin_stays_blocked_even_with_a_valid_csrf_token(studio, origin):
    csrf = browser(studio)
    response = studio.client.post("/projects", base_url=BASE,
        headers={"Origin": origin}, data={"csrf": csrf, "submission": "d" * 64})
    assert response.status_code == 403
    assert studio.calls == []
    assert not list(studio.manager.output.glob("dashboard-*"))
    if origin == "null":
        assert b"refresh the project page" in response.data


def test_cleanup_old_output_removes_only_old_finished_projects(tmp_path):
    # A small always-on server can fill its disk within days at 10 videos/day; cleanup must
    # remove old finished output but never touch anything in progress or still recent,
    # regardless of status.
    settings = Settings(tmp_path, copy.deepcopy(load_settings().raw))
    manager = DashboardStore(settings, synchronous=True)

    def make_project(id_, *, status, busy, age_days):
        directory = manager.output / f"dashboard-{id_}"
        directory.mkdir(parents=True)
        (directory / "scene-01.png").write_bytes(b"x")
        created_at = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
        save_json(directory / "dashboard-project.json",
                 {"id": id_, "status": status, "busy": busy, "created_at": created_at})
        return directory

    old_complete = make_project("a", status="complete", busy=False, age_days=20)
    old_failed = make_project("b", status="failed", busy=False, age_days=20)
    old_but_busy = make_project("c", status="running", busy=True, age_days=20)
    old_but_running = make_project("d", status="running", busy=False, age_days=20)
    recent_complete = make_project("e", status="complete", busy=False, age_days=1)

    removed = manager.cleanup_old_output(days=14)

    assert set(removed) == {"a", "b"}
    assert not old_complete.exists() and not old_failed.exists()
    assert old_but_busy.exists() and old_but_running.exists() and recent_complete.exists()


def test_local_origin_still_requires_csrf(studio):
    browser(studio)
    response = studio.client.post("/projects", base_url=BASE,
        headers={"Origin": BASE}, data={"submission": "e" * 64})
    assert response.status_code == 403 and studio.calls == []


def test_configurable_trusted_host_adds_one_origin_without_removing_127001(tmp_path, monkeypatch):
    # DASHBOARD_TRUSTED_HOST exists only for a deliberately exposed deployment behind a
    # TLS-terminating, authenticated reverse proxy — it must add exactly the configured extra
    # host(s), never weaken the default (unset) loopback-only behavior every other test relies on.
    import elsewhere.dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "TRUSTED_HOSTS", {"72.44.62.213"})
    settings = Settings(tmp_path, copy.deepcopy(load_settings().raw))
    app = dashboard_module.create_app(settings, store=DashboardStore(settings, synchronous=True))
    app.testing = True
    client = app.test_client()
    trusted = "https://72.44.62.213"
    assert client.get("/", base_url=trusted).status_code == 200
    assert client.get("/", base_url="http://127.0.0.1:8765").status_code == 200
    assert client.get("/", base_url="http://evil.example").status_code == 403
    with client.session_transaction(base_url=trusted) as session:
        csrf = session["csrf"]
    accepted = client.post("/projects", base_url=trusted,
        headers={"Origin": trusted}, data={"csrf": csrf, "submission": "z" * 64})
    assert accepted.status_code == 303
    with client.session_transaction(base_url=trusted) as session:
        csrf2 = session["csrf"]
    mismatched = client.post("/projects", base_url=trusted,
        headers={"Origin": "https://not-the-real-host.example"}, data={"csrf": csrf2, "submission": "y" * 64})
    assert mismatched.status_code == 403


def test_configurable_trusted_host_accepts_a_comma_separated_list(tmp_path, monkeypatch):
    import elsewhere.dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "TRUSTED_HOSTS", {"72.44.62.213", "72.44.62.213.nip.io"})
    settings = Settings(tmp_path, copy.deepcopy(load_settings().raw))
    app = dashboard_module.create_app(settings, store=DashboardStore(settings, synchronous=True))
    app.testing = True
    client = app.test_client()
    assert client.get("/", base_url="https://72.44.62.213").status_code == 200
    assert client.get("/", base_url="https://72.44.62.213.nip.io").status_code == 200
    assert client.get("/", base_url="http://evil.example").status_code == 403


def test_browser_approval_story_and_completion_pages(studio):
    csrf = browser(studio)
    project_id = new(studio)
    response = studio.client.post(f"/projects/{project_id}/generate", base_url=BASE,
        data={"csrf":csrf,"version":1,"paid_consent":"yes"})
    assert response.status_code == 303
    page = studio.client.get(response.location, base_url=BASE)
    assert page.status_code == 200 and b"Create Video From This Story" in page.data
    assert b"Your approval is final. Production will begin immediately." in page.data
    p = studio.manager.load(project_id)
    response = studio.client.post(f"/projects/{project_id}/approve", base_url=BASE,
        data={"csrf":csrf,"version":p["version"],"paid_consent":"yes"})
    complete = studio.client.get(response.location, base_url=BASE)
    assert complete.status_code == 200
    assert b"Download final.mp4" in complete.data and b"48000" in complete.data
    media = studio.client.get(f"/media/{project_id}/1/final.mp4", base_url=BASE, headers={"Range":"bytes=0-3"})
    assert media.status_code == 206 and media.data == b"MOCK"


def test_keys_payloads_and_path_traversal_are_not_exposed(studio, monkeypatch):
    secret = "sk-mock-secret-never-for-network"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    new(studio)
    act(studio, "generate")
    p = studio.manager.load(studio.project_id)
    p["message"] = secret
    studio.manager.save(p)
    response = studio.client.get(f"/api/projects/{p['id']}", base_url=BASE)
    assert secret.encode() not in response.data
    assert "visual_prompt" not in response.json["story"]["scenes"][0]
    assert studio.client.get(f"/media/{p['id']}/1/cost-report.json", base_url=BASE).status_code == 404
    assert studio.client.get(f"/media/{p['id']}/1/.env", base_url=BASE).status_code == 404
    assert studio.client.get("/projects/../../.env", base_url=BASE).status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_failed_project_has_private_data_free_downloadable_diagnostics(studio):
    project_id = new(studio)
    studio.ambiguous = True
    act(studio, "generate")
    report = studio.client.get(f"/projects/{project_id}/diagnostics.json", base_url=BASE)
    assert report.status_code == 200 and "attachment" in report.headers["Content-Disposition"]
    assert report.json["failures"][0]["diagnostic"]["automatic_retry"] is False
    assert "private SDK payload" not in report.text
    page = studio.client.get(f"/projects/{project_id}", base_url=BASE)
    assert b"Why production stopped" in page.data and b"Resume stays locked" in page.data
    assert studio.calls == ["story_generation"]
    with pytest.raises(BudgetExceeded):
        act(studio, "resume")


@pytest.mark.parametrize("values", [{"max_cost":"nan"},{"max_cost":-1},{"max_cost":1000},
                                     {"voice":"$(touch bad)"},{"story_type":"not an option"}, {"idea":"x"*1001}])
def test_backend_input_validation(values):
    with pytest.raises(ValueError):
        NewVideo.model_validate(values)


def test_existing_outputs_are_read_only(studio):
    old = studio.settings.path("output") / "20260101T000000Z-one-video-test"
    old.mkdir()
    save_json(old / "run-state.json", {"status":"complete","created_at":"2026-01-01"})
    (old / "final.mp4").write_bytes(b"EXISTING VIDEO")
    before = {p.name:p.read_bytes() for p in old.iterdir()}
    response = studio.client.get("/history", base_url=BASE)
    assert response.status_code == 200 and b"Existing local run" in response.data
    assert studio.client.get(f"/existing/{old.name}", base_url=BASE).status_code == 200
    assert before == {p.name:p.read_bytes() for p in old.iterdir()}


def test_story_request_schema_has_no_open_ended_objects():
    from openai.lib._pydantic import to_strict_json_schema

    from elsewhere.models import StoryDraftResponse
    schema = to_strict_json_schema(StoryDraftResponse)

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)


def test_entire_suite_network_guard_is_active():
    with pytest.raises(AssertionError, match="Network access is blocked"):
        socket.create_connection(("127.0.0.1", 8765))
    with pytest.raises(AssertionError, match="Network access is blocked"):
        socket.getaddrinfo("api.openai.com", 443)


def test_edit_discards_every_old_direction_and_plans_only_after_approval(studio):
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.load(project_id)
    old_dir = studio.manager.revision_dir(p)
    original = StoryPackage.model_validate_json((old_dir / "draft.json").read_text())
    sentinel = "STALE SPACE STATION AND OLD CAST MUST NEVER BE REUSED"
    original.continuity_bible = sentinel
    original.description = sentinel
    original.creative_fingerprint = dict.fromkeys(original.creative_fingerprint, sentinel)
    for scene in original.scenes:
        scene.visual_prompt = sentinel
    save_json(old_dir / "draft.json", original)
    save_json(old_dir / "visual-plan.json", {"old_direction": sentinel})
    before = {f.name: f.read_bytes() for f in old_dir.iterdir() if f.is_file()}
    studio.manager.edit(project_id, p["version"], "An Edited Wedding Photo", original.narration)
    edited_dir = studio.manager.revision_dir(studio.manager.load(project_id))
    edited = StoryPackage.model_validate_json((edited_dir / "draft.json").read_text())
    assert edited_dir != old_dir and not (edited_dir / "visual-plan.json").exists()
    assert edited.narration == original.narration
    assert edited.continuity_bible == "" and edited.creative_fingerprint == {}
    assert sentinel not in edited.model_dump_json()
    assert not studio.planned_stories and studio.calls == ["story_generation"]
    act(studio, "approve")
    assert studio.manager.public(project_id)["status"] == "complete"
    assert studio.reviewed_stories == []
    assert studio.planned_stories == [edited.model_dump()]
    plan = VisualPlan.model_validate_json((edited_dir / "visual-plan.json").read_text())
    assert studio.image_prompts == [plan.continuity_bible + "\n\n" + prompt for prompt in plan.prompts]
    assert all(sentinel not in prompt for prompt in studio.image_prompts)
    assert before == {f.name: f.read_bytes() for f in old_dir.iterdir() if f.is_file()}


def test_visual_planner_request_excludes_old_story_metadata(studio):
    new(studio)
    act(studio, "generate")
    p = studio.manager.load(studio.project_id)
    directory = studio.manager.revision_dir(p)
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    sentinel = "NEVER SEND THIS OLD CAST OR OLD LOCATION"
    story.continuity_bible = story.description = story.premise = sentinel
    for scene in story.scenes:
        scene.visual_prompt = sentinel
    captured = []
    plan = VisualPlan(continuity_bible="A complete mock continuity description for an offline test only.",
                      prompts=["Mock scene action and distinct framing number " + str(i) for i in range(8)])

    def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(output_parsed=plan, model="mock", usage={"input_tokens": 10, "output_tokens": 10})

    service = object.__new__(AIService)
    service.settings = studio.manager.settings(p)
    service.ledger = CostLedger(directory / "planner-test-cost.json", .5, service.settings.costs)
    service.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    assert service.prepare_image_prompts(story) == plan
    assert len(captured) == 1 and sentinel not in captured[0]["input"]
    assert json.dumps([s.narration for s in story.scenes]) in captured[0]["input"]


def test_resume_refuses_plan_for_a_different_approved_script(studio):
    new(studio)
    act(studio, "generate")
    studio.cancel_after = 1
    act(studio, "approve")
    directory = studio.manager.revision_dir(studio.manager.load(studio.project_id))
    save_json(directory / "visual-plan-source.json", {"sha256": "a stale story hash"})
    calls = list(studio.calls)
    studio.cancel_after = None
    act(studio, "resume")
    assert studio.manager.public(studio.project_id)["status"] == "failed"
    assert studio.calls == calls


def test_refresh_and_server_restart_recover_assets_and_costs_without_repeating(studio):
    project_id = new(studio)
    act(studio, "generate")
    studio.cancel_after = 1
    act(studio, "approve")
    snapshot = studio.manager.public(project_id)
    calls = list(studio.calls)
    for _ in range(3):
        assert studio.client.get(f"/projects/{project_id}", base_url=BASE).status_code == 200
        assert studio.client.get(f"/api/projects/{project_id}", base_url=BASE).json["images"] == ["scene-01.png"]
    studio.manager.shutdown()
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    app = create_app(studio.settings, store=restored)
    client = app.test_client()
    recovered = client.get(f"/api/projects/{project_id}", base_url=BASE).json
    for key in ("status", "stages", "requests", "calculated_cost", "images", "story", "revision"):
        assert recovered[key] == snapshot[key]
    assert studio.calls == calls
    studio.manager = restored
    studio.cancel_after = None
    act(studio, "resume")
    assert restored.public(project_id)["status"] == "complete"
    assert studio.calls.count("image_01") == studio.calls.count("image_prompts") == 1
    assert "quality_review" not in studio.calls


@pytest.mark.parametrize("category", list(RUBRICS))
def test_dashboard_shows_relevant_advisory_scores_without_review(studio, category):
    project_id = studio.manager.create(NewVideo(story_type=category), "f" * 64)
    studio.project_id = project_id
    act(studio, "generate")  # Mock fixture only; does not assess actual literary merit.
    p = studio.manager.public(project_id)
    assert p["selected_category"] == category
    assert set(p["evaluation"]["draft_category_scores"]) == set(RUBRICS[category][1])
    page = studio.client.get(f"/projects/{project_id}", base_url=BASE)
    assert page.status_code == 200
    for label in RUBRICS[category][1].values():
        assert label.encode() in page.data
    if category == "Funny":
        assert b"Comedy strength" in page.data and b"Punchline" in page.data
        assert b"Emotional stakes" not in page.data and b"Final twist" not in page.data
    if category == "Crime":
        # The unrelated wedding fixture has no intentional harmless wrongdoing.
        assert p["evaluation"]["draft_scores"]["category_match"] <= 3
        assert not p["evaluation"]["errors"]  # Category fit is advice, not a second approval.
    act(studio, "approve")
    p = studio.manager.public(project_id)
    assert p["review"] is None and p["status"] == "complete"
    assert "quality_review" not in studio.calls


def test_random_choice_is_saved_shown_and_stable_across_edit_regenerate_restart(studio, monkeypatch):
    choices = []

    def choose(categories):
        choices.append(list(categories))
        return "Funny"

    monkeypatch.setattr("elsewhere.dashboard_store.secrets.choice", choose)
    project_id = studio.manager.create(NewVideo(story_type="Random"), "a" * 64)
    studio.project_id = project_id
    confirmation = studio.client.get(f"/projects/{project_id}/confirm-story", base_url=BASE)
    assert b"Funny" in confirmation.data and studio.calls == []
    act(studio, "generate")
    p = studio.manager.public(project_id)
    studio.manager.edit(project_id, p["version"], "An Edited Mock Story", " ".join(s["narration"] for s in p["story"]["scenes"]))
    assert studio.manager.public(project_id)["evaluation"]["draft_category_scores"] == {}
    act(studio, "generate")
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    assert restored.public(project_id)["selected_category"] == "Funny"
    assert restored.history()[0]["type"] == "Funny (Random)"
    assert len(choices) == 1 and "Random" not in choices[0]
    assert studio.calls == ["story_generation", "story_generation"]


def test_legacy_project_metadata_migrates_without_rewriting_drafts_or_costs(studio):
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.load(project_id)
    directory = studio.manager.revision_dir(p)
    before = {str(f): f.read_bytes() for f in directory.rglob("*") if f.is_file()}
    del p["selected_category"]
    del p["rubric_version"]
    p["options"]["duration"] = "55-65"
    p["options"]["story_type"] = "Mystery with final twist"
    studio.manager.save(p)
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    current = restored.load(project_id)
    assert current["selected_category"] == "Mystery with Final Twist"
    assert restored.settings(current).formats["short"]["duration_max_seconds"] == 60
    assert before == {str(f): f.read_bytes() for f in directory.rglob("*") if f.is_file()}
    assert studio.calls == ["story_generation"]


def test_approval_revalidates_instead_of_trusting_cached_warnings(studio):
    project_id = new(studio)
    act(studio, "generate")
    directory = studio.manager.revision_dir(studio.manager.load(project_id))
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    story.hook = "This hook does not match the first scene narration."
    save_json(directory / "draft.json", story)
    save_json(directory / "draft-evaluation.json", {"warnings": []})
    with pytest.raises(ValueError, match="validation errors"):
        act(studio, "approve")
    assert studio.calls == ["story_generation"]


def test_human_approval_overrides_136_word_target_without_rewriting(studio):
    project_id = new(studio)
    act(studio, "generate")
    directory = studio.manager.revision_dir(studio.manager.load(project_id))
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    extra = 136 - story.word_count
    assert extra > 0
    story.scenes[-1].narration += " " + " ".join(["Then"] * extra)
    story.narration = " ".join(s.narration for s in story.scenes)
    save_json(directory / "draft.json", story)
    assert story.word_count == 136
    current = studio.manager.public(project_id)
    assert not current["evaluation"]["errors"]
    assert any("Advisory only" in w for w in current["evaluation"]["warnings"])
    act(studio, "approve")
    assert studio.manager.public(project_id)["status"] == "complete"
    assert "quality_review" not in studio.calls
    assert studio.calls.count("story_generation") == 1
    saved = StoryPackage.model_validate_json((directory / "story.json").read_text())
    assert saved.narration == story.narration


def test_new_duration_choices_never_exceed_sixty_seconds(studio):
    from elsewhere.dashboard_store import DURATIONS
    assert all(high <= 60 for low, high in DURATIONS.values())
    with pytest.raises(ValueError):
        NewVideo(duration="55-65")


def test_low_or_missing_editorial_scores_never_override_human_approval(studio):
    new(studio)
    act(studio, "generate")
    directory = studio.manager.revision_dir(studio.manager.load(studio.project_id))
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    story.draft_scores = dict.fromkeys(EditorialScores.model_fields, 1)
    story.draft_category_scores = []
    save_json(directory / "draft.json", story)
    act(studio, "approve")
    assert studio.manager.public(studio.project_id)["status"] == "complete"
    assert studio.calls.count("story_generation") == 1 and "quality_review" not in studio.calls


def test_review_request_is_blocked_before_any_charge_or_client_use(studio):
    new(studio)
    with pytest.raises(ValueError, match="review is disabled"):
        studio.manager.guard(studio.project_id, .03, "quality_review")
    service = object.__new__(AIService)
    service.settings = studio.manager.settings(studio.manager.load(studio.project_id))
    # No client or ledger is installed: the function must exit before touching either.
    with pytest.raises(RuntimeError, match="review is disabled"):
        service.review_story(None)
    assert studio.calls == []


def test_progress_and_estimate_have_no_review_stage_or_future_review_cost(studio):
    new(studio)
    p = studio.manager.public(studio.project_id)
    assert "quality_review" not in {s["key"] for s in p["stages"]}
    assert p["estimated_remaining"] == pytest.approx(8 * .015 + .02 + .006 + .008)


def test_tampering_after_final_approval_cannot_replace_the_story_on_resume(studio):
    new(studio)
    act(studio, "generate")
    studio.cancel_after = 1
    act(studio, "approve")
    directory = studio.manager.revision_dir(studio.manager.load(studio.project_id))
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    story.title = "A Different Story Title"
    save_json(directory / "draft.json", story)
    before = list(studio.calls)
    studio.cancel_after = None
    act(studio, "resume")
    assert studio.manager.public(studio.project_id)["status"] == "failed"
    assert studio.calls == before


def test_new_cast_project_saves_disclosure_flags_plans_and_reuses_media(studio, monkeypatch):
    from elsewhere.recurring_cast import CAST, DISCLOSURE, FLAGS, VISUAL_IDENTITIES
    studio.settings.raw["recurring_cast"] = {"version": 1, "main_characters": list(CAST)}
    original_generate = studio.service_factory.create_story

    def mock_cast_story(service, *args):
        story = original_generate(service, *args)
        # Repeated test-only template, deliberately not a publishable candidate script.
        for scene, color in zip(story.scenes, ["red", "green", "white", "black", "pink", "blue", "gray", "brown"], strict=True):
            scene.narration = f"Messi and Ronaldo checked the {color} box. They found a small soft blue cloth inside."
        story.hook = story.scenes[0].narration
        story.narration = " ".join(s.narration for s in story.scenes)
        return story

    monkeypatch.setattr(studio.service_factory, "create_story", mock_cast_story)
    project_id = new(studio)
    p = studio.manager.load(project_id)
    assert all(p[k] is True for k in FLAGS)
    assert p["recurring_cast"]["main_characters"] == list(CAST)
    assert "fake photograph" in studio.manager.settings(p).brand["visual_style"]
    act(studio, "generate")
    directory = studio.manager.revision_dir(studio.manager.load(project_id))
    draft = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    assert draft.youtube_disclosure == DISCLOSURE and DISCLOSURE not in draft.description
    assert (directory / "narration-performance-plan.json").exists()
    page = studio.client.get(f"/projects/{project_id}", base_url=BASE)
    assert b"Recurring cast roster: Messi, Ronaldo, IShowSpeed and MrBeast" in page.data
    assert b"Narration: AI storyteller, not their real voices" in page.data
    assert b"Realistic synthetic public figures: YouTube disclosure required" in page.data
    studio.fail_render = True
    act(studio, "approve")
    calls = list(studio.calls)
    studio.fail_render = False
    act(studio, "resume")
    assert studio.calls == calls and calls.count("speech_generation") == 1
    assert studio.manager.public(project_id)["status"] == "complete"
    assert all(VISUAL_IDENTITIES in prompt for prompt in studio.image_prompts)
    metadata = json.loads((directory / "production-metadata.json").read_text())
    assert all(metadata[k] is True for k in FLAGS) and metadata["description"].endswith(DISCLOSURE)
    assert len(json.loads((directory / "image-prompts.json").read_text())["prompts"]) == 8
    # Neither restart nor history viewing may touch any completed file.
    root = studio.manager.directory(project_id)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    restored.public(project_id)
    restored.history()
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_existing_project_keeps_old_cast_until_explicit_new_generation(studio):
    from elsewhere.recurring_cast import CAST
    project_id = new(studio)
    act(studio, "generate")
    p = studio.manager.load(project_id)
    draft = studio.manager.revision_dir(p) / "draft.json"
    before = draft.read_bytes()
    studio.settings.raw["recurring_cast"] = {"version": 1, "main_characters": list(CAST)}
    restored = DashboardStore(studio.settings, service_factory=studio.service_factory, synchronous=True)
    assert not restored.public(project_id)["recurring_cast"]
    assert "recurring_cast" not in restored.settings(restored.load(project_id)).raw
    assert draft.read_bytes() == before
    restored.dispatch(project_id, "generate", p["version"])
    assert restored.load(project_id)["recurring_cast"]["main_characters"] == list(CAST)
    assert draft.read_bytes() == before
