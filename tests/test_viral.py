"""Viral selection/language integration uses only mocked feeds and services."""
import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from test_dashboard import studio  # noqa: F401 - shared mocked pipeline fixture

from elsewhere.captions import align_story_words, caption_records, write_ass, write_srt
from elsewhere.config import Settings, load_settings
from elsewhere.costs import CostLedger
from elsewhere.dashboard import create_app
from elsewhere.dashboard_store import DashboardStore, NewVideo
from elsewhere.languages import (
    analyze_language,
    caption_font,
    suggest_language,
    unicode_token,
)
from elsewhere.models import NarrationAlignment, StoryPackage, TimedWord
from elsewhere.openai_service import AIService, save_json
from elsewhere.performance import make_plan
from elsewhere.prompts import story_prompt, system_prompt, viral_direction
from elsewhere.renderer import synthetic_alignment
from elsewhere.safety import SafetyError, local_checks
from elsewhere.scene_plan import narration_scenes
from elsewhere.trends import (
    WORLD_REGIONS,
    TrendError,
    TrendStore,
    parse_feed,
    rank_topics,
    traffic_lower_bound,
)
from elsewhere.youtube_dashboard import YouTubeDashboard

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
HINDI = "आज मेरी लाल गेंद घर से बाहर भाग गई। मैं उसके पीछे गया और दरवाजा खुला पाया।"


def feed(now, title="Indian paper kite festival", age=1):
    date = format_datetime(now - timedelta(hours=age))
    return f"<rss><channel><item><title>{title}</title><pubDate>{date}</pubDate></item></channel></rss>".encode()


def setup(tmp_path):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    return Settings(tmp_path, raw)


def viral_project(tmp_path, language="auto"):
    settings = setup(tmp_path)
    trends = TrendStore(tmp_path, fetcher=lambda _: feed(datetime.now(UTC)))
    snapshot = trends.lookup("IN")
    manager = DashboardStore(settings, synchronous=True)
    options = NewVideo(video_mode="viral", language=language, country="IN",
                       trend_snapshot=snapshot["id"], trend_id=snapshot["records"][0]["id"])
    return manager, manager.create(options, "a" * 64), options


def test_viral_project_can_be_created_with_no_trend_at_all(tmp_path):
    # A real trend is optional bonus seasoning for the locked GTA6 topic now, not a
    # requirement — most days no live trend will even be about GTA6.
    manager = DashboardStore(setup(tmp_path), synchronous=True)
    pid = manager.create(NewVideo(video_mode="viral"), "b" * 64)
    project = manager.load(pid)
    assert project["trend"] is None
    assert project["niche"] == "Gaming"
    assert manager.settings(project).raw["dashboard_brief"]["niche"] == project["niche"]


def test_viral_gaming_content_may_name_real_games_blocked_terms_still_bans_named_people(tmp_path):
    # Regression: blocked_terms exists to stop the FICTION engine writing disguised franchise
    # fan-fiction. Real Gaming-niche content legitimately names real games like Minecraft —
    # that check must not fire for viral mode, while unrelated named-individual terms still do.
    manager, pid, _ = viral_project(tmp_path)
    settings = manager.settings(manager.load(pid))
    base = ("Minecraft's creeper began as a coding mistake in early development. A developer tried "
            "to build a pig, but the model's height and width got swapped by accident. Instead of a "
            "pig, the game produced a tall, strange green creature that players now fear.")
    scenes = narration_scenes(base)
    story = hindi_story().model_copy(update={"narration": base, "story_category": "Game Origin Story",
        "scenes": scenes, "hook": scenes[0].narration})
    local_checks(story, settings, [], advisory_editorial=True)  # must not raise for "Minecraft"
    tagged = base + " Donald Trump once mentioned playing it on television during an interview segment."
    scenes = narration_scenes(tagged)
    political = story.model_copy(update={"narration": tagged, "scenes": scenes, "hook": scenes[0].narration})
    with pytest.raises(SafetyError, match="Donald Trump"):
        local_checks(political, settings, [], advisory_editorial=True)


def test_viral_niche_is_always_gaming(tmp_path):
    manager = DashboardStore(setup(tmp_path), synchronous=True)
    first = manager.load(manager.create(NewVideo(video_mode="viral"), "c" * 64))
    second = manager.load(manager.create(NewVideo(video_mode="viral"), "d" * 64))
    assert first["niche"] == "Gaming"
    assert second["niche"] == "Gaming"


def test_viral_direction_locks_to_gta6_only():
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    raw["dashboard_brief"] = {"video_mode": "viral", "niche": "Gaming", "trend": None}
    prompt = viral_direction(Settings(ROOT, raw))
    assert "LOCKED TO GTA6" in prompt
    assert "GTA6" in prompt
    assert "REAL, factual" in prompt


def test_viral_repeated_setting_and_characters_are_not_flagged_as_too_similar(tmp_path):
    # GTA6 is the only real topic Viral Material is locked to: its real-world setting, and
    # often its named characters (Lucia/Jason), are legitimately the same story to story.
    # That must not trip the duplicate-idea warning the way it correctly would for fiction.
    def story_with(sentence, fingerprint):
        # Distinct narration per case: the broader narration-similarity check must not be
        # what's (dis)proving this fingerprint-specific behavior.
        text = " ".join(f"{sentence}, part {i}." for i in range(8))
        scenes = narration_scenes(text)
        return hindi_story().model_copy(update={"narration": text, "scenes": scenes,
                                                 "hook": scenes[0].narration, "creative_fingerprint": fingerprint})

    manager, pid, _ = viral_project(tmp_path)
    project = manager.load(pid)
    previous = story_with("A leaked trailer clip surfaced online", {
        "main_object": "a leaked trailer clip", "setting": "Vice City, Leonida",
        "characters": "Lucia and Jason", "twist": "the leak turns out to be official marketing"})
    (manager.output / "dashboard-previous" / "revision-0001").mkdir(parents=True)
    save_json(manager.output / "dashboard-previous" / "revision-0001" / "draft.json", previous.model_dump())

    same_setting_and_cast = story_with("The game's radio station lineup was revealed", {
        "main_object": "the game's radio station lineup", "setting": "Vice City, Leonida",
        "characters": "Lucia and Jason", "twist": "a real musician's song was cut for licensing"})
    result = manager.evaluate(same_setting_and_cast, project)
    assert not any("Too similar" in w for w in result["warnings"])

    same_main_object = story_with("Fans spotted something wild in the newest footage", {
        "main_object": "a leaked trailer clip", "setting": "a completely different place",
        "characters": "no one in particular", "twist": "a completely different ending"})
    result = manager.evaluate(same_main_object, project)
    assert any("Too similar to an earlier story: main_object" in w for w in result["warnings"])


def test_viral_visual_style_allows_real_people_but_never_logos(tmp_path):
    manager, pid, _ = viral_project(tmp_path)
    style = manager.settings(manager.load(pid)).brand["visual_style"]
    assert "real people" not in style
    assert "no text, logos, or copyrighted characters" in style


def hindi_story():
    # Clearly marked test data, not a new generated story.
    story = StoryPackage.model_validate_json((ROOT / "stories/wedding-photo-test.json").read_text())
    text = " ".join(f"यह नमूना वाक्य {i} है और हम इसे केवल जांच रहे हैं।" for i in range(8))
    scenes = narration_scenes(text)
    return story.model_copy(update={"title": "केवल जांच का नमूना", "narration": text,
                                   "scenes": scenes, "hook": scenes[0].narration})


def test_feed_age_source_no_false_youtube_ranking():
    now = datetime.now(UTC)
    rows = parse_feed(feed(now), "IN", now)
    assert rows[0]["suggested_language"] == "hi"
    assert "not YouTube" in rows[0]["source"]
    assert parse_feed(feed(now, age=25), "IN", now) == []
    assert parse_feed(feed(now, age=-1), "IN", now) == []
    for data in (b"<!DOCTYPE rss><rss/>", b"\x00<rss/>", b"not XML"):
        with pytest.raises(TrendError):
            parse_feed(data, "IN", now)


def test_lookup_cached_bounded_separate_api_key(tmp_path, monkeypatch):
    key = "mock-backend-key"
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", key)
    calls = []
    def fetch(url, api_key=None):
        calls.append((url, api_key))
        if "trends.google.com" in url:
            items = "".join(feed(datetime.now(UTC), title=f"Kites {i}").decode().split("<channel>")[1].split("</channel>")[0] for i in range(6))
            return f"<rss><channel>{items}</channel></rss>".encode()
        return json.dumps({"items": [{"id": {"videoId": "abcdefghijk"}, "snippet": {"title": "Mock kite video"}}]}).encode()
    trends = TrendStore(tmp_path, fetcher=fetch)
    first = trends.lookup("IN")
    assert trends.lookup("IN")["id"] == first["id"]
    assert len(calls) == 4 and all(k == key for _, k in calls[1:])
    assert all(key not in url for url, _ in calls)
    assert key not in json.dumps(first)
    assert first["calculated_cost_usd"] == 0
    assert len(first["records"][0]["youtube"]) == 1
    assert "three-search" in first["records"][-1]["youtube_status"]


@pytest.mark.parametrize(("topic", "code"), [("Indian kite festival", "hi"), ("Chennai kites", "ta"),
                         ("Lahore kite festival", "ur"), ("Kites", "en"), ("Bollywood film", "hi"),
                         ("India Pakistan cricket", "en"), ("Space telescope", "en")])
def test_language_suggestions(topic, code):
    assert suggest_language(topic)[0] == code


def test_new_page_and_viral_confirmation_no_paid_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    settings = setup(tmp_path)
    app = create_app(settings)
    app.extensions["trend_store"].fetcher = lambda _: feed(datetime.now(UTC))
    client = app.test_client()
    page = client.get("/", base_url=BASE)
    assert b"Viral" in page.data and b'<select name="country">' not in page.data
    with client.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    result = client.post("/trends", base_url=BASE, data={"csrf": csrf, "country": "IN"})
    assert result.status_code == 200 and b"Google Search trends" in result.data
    snapshot = app.extensions["trend_store"].lookup()
    data = {"csrf": csrf, "submission": "b" * 64, "video_mode": "viral",
            "trend_snapshot": snapshot["id"], "trend_id": snapshot["records"][0]["id"]}
    response = client.post("/projects", base_url=BASE, data=data)
    assert response.status_code == 303
    assert client.post("/projects", base_url=BASE, data=data).location == response.location
    confirmation = client.get(response.location, base_url=BASE)
    assert confirmation.status_code == 200 and "Hindi" in confirmation.text
    assert "Newly generated stories use Messi" not in confirmation.text
    assert "paid OpenAI" in confirmation.text
    projects = app.extensions["dashboard_store"].history()
    assert len(projects) == 1


def test_saved_mode_budget_restart_and_native_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    manager, pid, options = viral_project(tmp_path)
    project = manager.load(pid)
    assert project["language"] == "hi" and not project.get("recurring_cast")
    settings = manager.settings(project)
    assert caption_font(settings) == "Devanagari Sangam MN"
    assert settings.raw["dashboard_brief"]["video_mode"] == "viral"
    story = hindi_story()
    evaluation = manager.evaluate(story, project, compare=False)
    assert evaluation["readability"]["flesch_reading_ease"] is None
    assert evaluation["readability"]["words"] == story.word_count
    assert evaluation["errors"] == []
    save_json(manager.revision_dir(project) / "draft.json", story)
    save_json(manager.revision_dir(project) / "draft-evaluation.json", evaluation)
    project.update(status="story_ready")
    manager.save(project)
    restarted = DashboardStore(setup(tmp_path), synchronous=True)
    assert restarted.settings(restarted.load(pid)).raw == settings.raw
    public = restarted.public(pid)
    assert public["calculated_cost"] == 0
    assert public["remaining_allowance"] == options.max_cost
    assert public["trend"]["research_cost_usd"] == 0
    page = create_app(setup(tmp_path), store=restarted).test_client().get(f"/projects/{pid}", base_url=BASE)
    assert page.status_code == 200 and "Not applicable" in page.text
    assert "Create Video From This Story" in page.text
    local_checks(story, settings, [], advisory_editorial=True)


def test_override_and_generation_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    manager, pid, _ = viral_project(tmp_path, "en")
    project = manager.load(pid)
    assert project["language"] == "en"
    prompt = story_prompt(manager.settings(project), "short", [])
    assert "VIRAL MATERIAL MODE" in prompt and "untrusted DATA" in prompt
    assert "Indian paper kite festival" in prompt and "not fiction" in prompt
    assert "VERY catchy and clickable" in prompt
    raw = manager.settings(project).raw
    raw["brand"]["language"] = "hi"
    prompt = system_prompt(Settings(tmp_path, raw))
    assert "native script" in prompt and "Flesch Reading Ease >=85" not in prompt


def test_topic_language_is_independent_of_feed_country():
    now = datetime.now(UTC)
    for country in ("IN", "PK", "US", "GB"):
        assert parse_feed(feed(now, "Space telescope"), country, now)[0]["suggested_language"] == "en"
        assert parse_feed(feed(now, "Bollywood film"), country, now)[0]["suggested_language"] == "hi"
        assert parse_feed(feed(now, "Lahore kite festival"), country, now)[0]["suggested_language"] == "ur"


def test_automatic_discovery_partial_failure_dedup_and_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    calls = []
    def fetch(url):
        calls.append(url)
        if url.endswith("PK"):
            raise TrendError("Unavailable mock source")
        return feed(datetime.now(UTC), "Chennai kite festival")
    store = TrendStore(tmp_path, fetcher=fetch)
    snapshot = store.lookup()
    assert len(calls) == len(WORLD_REGIONS) and len(snapshot["records"]) == 1
    assert snapshot["unavailable_sources"] == ["PK"]
    assert snapshot["records"][0]["suggested_language"] == "ta"
    assert store.lookup()["id"] == snapshot["id"] and len(calls) == len(WORLD_REGIONS)


def test_worldwide_rank_uses_volume_and_recency_not_feed_order():
    now = datetime.now(UTC)
    old = parse_feed(feed(now, "Older big topic", age=20), "US", now)[0]
    old["search_volume_lower_bound"] = 100_000
    rising = parse_feed(feed(now, "New rising topic", age=1), "BR", now)[0]
    rising["search_volume_lower_bound"] = 20_000
    unknown = parse_feed(feed(now, "Unknown topic", age=0), "ZA", now)[0]
    ranked = rank_topics([[old, unknown], [rising]], now)
    assert ranked[0]["title"] == "New rising topic"
    assert ranked[-1]["momentum_estimate"] is None
    assert ranked[-1]["missing_volume_regions"] == 1
    assert "Not measured growth" in ranked[0]["ranking_reason"]
    assert {"BR", "ZA", "JP", "AU", "FR", "US", "IN"} <= set(WORLD_REGIONS)


def test_traffic_is_parsed_and_unknown_not_zero():
    assert traffic_lower_bound("200K+") == 200000
    assert traffic_lower_bound("1,000+") == 1000
    assert traffic_lower_bound("1.5M+") == 1500000
    assert traffic_lower_bound("missing") is None
    now = datetime.now(UTC)
    xml = feed(now).decode().replace('<rss>', '<rss xmlns:ht="https://trends.google.com/trending/rss">')
    xml = xml.replace('</item>', '<ht:approx_traffic>20K+</ht:approx_traffic></item>')
    assert parse_feed(xml.encode(), "IN", now)[0]["search_volume_lower_bound"] == 20000


def test_recommendation_auto_selects_one_topic_and_language_without_generation(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    app = create_app(setup(tmp_path))
    calls = []
    def fetch(url):
        calls.append(url)
        return feed(datetime.now(UTC), "Bollywood kite festival")
    app.extensions["trend_store"].fetcher = fetch
    client = app.test_client()
    client.get("/", base_url=BASE)
    assert calls == []  # Page load must not start free or paid research.
    with client.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    result = client.post("/api/trends/recommendation", base_url=BASE, data={"csrf": csrf})
    assert result.status_code == 200
    data = result.json
    assert data["topic"]["title"] == "Bollywood kite festival"
    assert data["topic"]["suggested_language"] == "hi"
    assert data["sources_attempted"] == len(WORLD_REGIONS)
    assert data["calculated_cost_usd"] == 0
    assert app.extensions["dashboard_store"].history() == []
    assert client.post("/api/trends/recommendation", base_url=BASE, data={"csrf": csrf}).json["snapshot_id"] == data["snapshot_id"]
    assert len(calls) == len(WORLD_REGIONS)
    assert client.post("/api/trends/recommendation", base_url=BASE).status_code == 403
    page = client.post("/trends", base_url=BASE, data={"csrf": csrf})
    assert page.status_code == 200 and page.text.count('checked required') == 1


def test_recommendation_no_topics_does_not_invent_a_winner(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    app = create_app(setup(tmp_path))
    app.extensions["trend_store"].fetcher = lambda _: b"<rss><channel/></rss>"
    client = app.test_client()
    client.get("/", base_url=BASE)
    with client.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    result = client.post("/api/trends/recommendation", base_url=BASE, data={"csrf": csrf})
    assert result.json["topic"] is None and result.json["language_name"] is None


def test_browser_viral_selection_logic_with_mocked_dom_and_fetch():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node needed for the mocked browser event test")
    subprocess.run([node, "--test", str(ROOT / "tests/dashboard_viral.test.cjs")],
                   check=True, capture_output=True, timeout=15)


def test_old_snapshot_country_guess_does_not_override_topic_on_new_selection(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    store = TrendStore(tmp_path, fetcher=lambda _: feed(datetime.now(UTC), "Space telescope"))
    snapshot = store.lookup("IN")
    snapshot["records"][0].update(suggested_language="hi", language_reason="Old country default")
    save_json(store.directory / (snapshot["id"] + ".json"), snapshot)
    selected = store.selection(snapshot["id"], snapshot["records"][0]["id"])
    assert selected["suggested_language"] == "en"
    assert selected["language_selection"] == "topic_context_v2"


def test_stale_and_forged_selection_rejected_without_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    trends = TrendStore(tmp_path, fetcher=lambda _: feed(datetime.now(UTC)))
    snapshot = trends.lookup("IN")
    with pytest.raises(TrendError):
        trends.selection("../bad", "a" * 32)
    snapshot["records"][0]["published_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    save_json(trends.directory / (snapshot["id"] + ".json"), snapshot)
    with pytest.raises(TrendError, match="older than"):
        trends.selection(snapshot["id"], snapshot["records"][0]["id"])


def test_unicode_alignment_captions_and_plan(tmp_path):
    story = hindi_story()
    alignment = synthetic_alignment(story, 55)
    records = caption_records(story.narration, alignment, 4)
    assert " ".join(t for _, _, t in records).split() == story.narration.split()
    assert all(len(t.splitlines()) <= 2 and all(len(line) <= 28 for line in t.splitlines()) for _, _, t in records)
    assert unicode_token("गेंद।") == "गेंद" and unicode_token("गेंद") != unicode_token("घर")
    bad = NarrationAlignment(model="mock", transcript="घर घर घर घर", duration_seconds=55,
                             words=[TimedWord(word="घर", start=0, end=1)])
    with pytest.raises(ValueError, match="matched only"):
        align_story_words(story.narration, bad)
    settings = setup(tmp_path)
    settings.raw["brand"]["language"] = "hi"
    assert " ".join(s["sentence_text"] for s in make_plan(HINDI, settings)["sentences"]) == HINDI
    assert analyze_language(HINDI, "hi")["metrics"]["flesch_reading_ease"] is None
    write_srt(story.narration, alignment, 4, tmp_path / "captions.srt")
    write_ass(story.narration, alignment, 4, tmp_path / "captions.ass", font_name=caption_font(settings))
    assert "Devanagari Sangam MN" in (tmp_path / "captions.ass").read_text()
    assert "नमूना" in (tmp_path / "captions.srt").read_text()


def test_native_tts_and_transcription_parameters_one_request(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    manager, pid, _ = viral_project(tmp_path)
    settings = manager.settings(manager.load(pid))
    service = object.__new__(AIService)
    service.settings = settings
    service.ledger = CostLedger(tmp_path / "cost-report.json", .5, settings.costs)
    calls = []
    def speech(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parse=lambda: SimpleNamespace(write_to_file=lambda p: p.write_bytes(b"MOCK")))
    class Response(SimpleNamespace):
        def model_dump(self, **kwargs):
            return {"text": self.text, "words": [vars(w) for w in self.words], "usage": self.usage}
    def align(**kwargs):
        calls.append({k: v for k, v in kwargs.items() if k != "file"})
        return Response(text=HINDI, words=[SimpleNamespace(word=w, start=i, end=i+.5) for i,w in enumerate(HINDI.split())], usage={"seconds": 55})
    service.client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(with_raw_response=SimpleNamespace(create=speech)),
                                                         transcriptions=SimpleNamespace(create=align)))
    destination = tmp_path / "speech.wav"
    service.create_speech(HINDI, destination)
    service.align_speech(destination, 55)
    assert len(calls) == 2 and calls[0]["input"] == HINDI
    assert "Hindi" in calls[0]["instructions"]
    assert calls[1]["language"] == "hi" and calls[1]["timestamp_granularities"] == ["word"]
    assert (tmp_path / "narration-performance-plan.json").exists()


def test_upload_language_uses_video_not_channel_default(tmp_path):
    settings = setup(tmp_path)
    youtube_studio = YouTubeDashboard(settings, DashboardStore(settings))
    assert youtube_studio.video_settings({"language": "hi"}).brand["language"] == "hi"
    assert settings.brand["language"] == "en"


def test_narration_plan_matches_synced_category_without_an_edit_in_between(studio, monkeypatch):  # noqa: F811 - imported pytest fixture
    """Regression test: generate() used to save narration-performance-plan.json using the
    pre-generation settings (selected_category still ""), then sync the model's own chosen
    real-content category onto the project AFTER saving. A later create_speech() call
    recomputes the same plan from current (now-synced) settings and rejects any mismatch as
    tampering, so approving a freshly generated story straight through (no edit() in between,
    which happened to recompute and overwrite the plan with already-synced settings and thus
    masked the bug) hit a spurious 'Saved narration performance plan differs' failure."""
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    snapshot = TrendStore(studio.settings.root, fetcher=lambda _: feed(datetime.now(UTC))).lookup("IN")
    manager = studio.manager
    pid = manager.create(NewVideo(video_mode="viral", trend_snapshot=snapshot["id"],
                                 trend_id=snapshot["records"][0]["id"]), "f" * 64)
    manager.dispatch(pid, "generate", manager.load(pid)["version"])
    project = manager.load(pid)
    assert project["selected_category"]  # synced from the model's own reported story_category
    directory = manager.revision_dir(project)
    plan = json.loads((directory / "narration-performance-plan.json").read_text())
    assert plan["category"] == project["selected_category"]
    story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
    # The exact check create_speech() performs: recomputing the plan from current settings
    # must reproduce the saved one exactly, or it raises "differs from the approved text".
    assert make_plan(story.narration, manager.settings(project)) == plan


def test_viral_edit_approval_cancel_and_resume_reuses_paid_assets(studio, monkeypatch):  # noqa: F811 - imported pytest fixture
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    snapshot = TrendStore(studio.settings.root, fetcher=lambda _: feed(datetime.now(UTC))).lookup("IN")
    manager = studio.manager
    pid = manager.create(NewVideo(video_mode="viral", trend_snapshot=snapshot["id"],
                                 trend_id=snapshot["records"][0]["id"]), "e" * 64)
    studio.project_id = pid
    def act(action):
        manager.dispatch(pid, action, manager.load(pid)["version"])
    act("generate")
    assert studio.calls == ["story_generation"]
    story = hindi_story()
    manager.edit(pid, manager.load(pid)["version"], story.title, story.narration)
    assert studio.calls == ["story_generation"]
    studio.cancel_after = 2
    act("approve")
    assert manager.load(pid)["status"] == "cancelled"
    assert studio.planned_stories[0]["narration"] == story.narration
    assert "image_03" not in studio.calls
    assert manager.public(pid)["language"] == "hi"
    studio.cancel_after = None
    act("resume")
    assert manager.load(pid)["status"] == "complete"
    assert studio.calls.count("image_01") == 1 and studio.calls.count("story_generation") == 1
    assert "quality_review" not in studio.calls
    assert manager.public(pid)["calculated_cost"] < .5


@pytest.mark.parametrize(("code", "text"), [("hi", "यह केवल जांच का नमूना है।"),
                         ("ta", "இது ஒரு சோதனை மாதிரி மட்டுமே."), ("ur", "یہ صرف جانچ کے لیے ہے۔")])
def test_native_caption_font_renders_inside_safe_area(tmp_path, code, text):
    full_ffmpeg = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    ffmpeg = str(full_ffmpeg) if full_ffmpeg.is_file() else shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed for the offline single-frame caption test")
    settings = setup(tmp_path)
    settings.raw["brand"]["language"] = code
    words = text.split()
    alignment = NarrationAlignment(model="mock", transcript=text, duration_seconds=10,
        words=[TimedWord(word=w, start=i, end=i+.8) for i,w in enumerate(words)])
    ass = tmp_path / "test.ass"
    write_ass(text, alignment, 4, ass, font_name=caption_font(settings))
    png = tmp_path / "caption.png"
    # One synthetic subtitle test frame, not a generated story or video.
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=1080x1920:d=1",
                    "-vf", f"ass={ass}", "-frames:v", "1", str(png)], check=True, capture_output=True)
    with Image.open(png) as image:
        bounds = image.convert("L").point(lambda value: 255 if value > 50 else 0).getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    assert 95 <= left < right <= 985
    assert 120 <= top < bottom <= 1575
