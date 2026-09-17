import copy
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from elsewhere import youtube
from elsewhere.config import Settings, load_settings
from elsewhere.dashboard import create_app
from elsewhere.openai_service import save_json
from elsewhere.youtube_dashboard import YouTubeDashboard

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
CHANNEL = "UC" + "a" * 22
OTHER = "UC" + "b" * 22
VIDEO = "abcdefghijk"
PRIVATE = "private-test-credential-never-expose"


@pytest.fixture
def yt(tmp_path, monkeypatch):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    settings = Settings(tmp_path, raw)
    app = create_app(settings)
    app.testing = True
    studio = app.extensions["youtube_dashboard"]
    state = SimpleNamespace(settings=settings, app=app, studio=studio, browser=app.test_client(),
                            channels=[], inserts=[], chunks=0, oauth=0, wrong=False, fail=False,
                            status_checks=[], live_status="private", thumbnails=[], thumbnail_error=None)

    class Client:
        def channels(self):
            return self

        def videos(self):
            return self

        def thumbnails(self):
            return self

        def set(self, **kwargs):
            state.thumbnails.append(kwargs)
            def execute(**_):
                if state.thumbnail_error:
                    raise state.thumbnail_error
                return {}
            return SimpleNamespace(execute=execute)

        def list(self, **kwargs):
            if kwargs.get("part") == "status" and "id" in kwargs:
                state.status_checks.append(kwargs)
                items = [{"status": {"privacyStatus": state.live_status}}] if kwargs["id"] == VIDEO else []
                return SimpleNamespace(execute=lambda **_: {"items": items})
            state.channels.append(kwargs)
            identifier = OTHER if kwargs.get("mine") and state.wrong else CHANNEL
            return SimpleNamespace(execute=lambda **_: {"items": [{"id": identifier,
                "snippet": {"title": "One Minute Elsewhere", "customUrl": "@wrong" if identifier == OTHER else youtube.EXPECTED_HANDLE}}]})

        def insert(self, **kwargs):
            state.inserts.append(kwargs)
            return self

        def next_chunk(self, num_retries=0):
            assert num_retries == 0
            state.chunks += 1
            if state.fail:
                raise TimeoutError(PRIVATE)
            return None, {"id": VIDEO, "status": {"privacyStatus": "private"}}

    state.client = Client()
    monkeypatch.setattr(youtube, "youtube_client", lambda _: state.client)

    def authorize(settings):
        state.oauth += 1
        youtube.save_token(youtube.secret_path(settings, "youtube_token"), PRIVATE)

    monkeypatch.setattr(youtube, "authorize", authorize)
    folder = settings.path("output") / "existing-offline-test"
    folder.mkdir()
    (folder / "final.mp4").write_bytes(b"MOCK VIDEO - never sent over a network")
    (folder / "scene-01.png").write_bytes(b"MOCK THUMBNAIL CANDIDATE IMAGE - never sent over a network")
    (folder / "story.json").write_bytes((ROOT / "stories/wedding-photo-test.json").read_bytes())
    save_json(folder / "validation.json", {"checks": {"mock_offline": True}})
    state.video_path = folder / "final.mp4"
    state.key = studio.videos()[0]["key"]
    return state


def connect(yt):
    yt.studio.connection_action("connect", "a" * 64)
    assert yt.studio.status()["verified"]


def test_connection_page_is_offline_and_upload_controls_wait_for_verified_channel(yt):
    for _ in range(2):
        response = yt.browser.get("/youtube", base_url=BASE)
        assert response.status_code == 200
        assert b"Connect YouTube" in response.data and b"No upload occurs" in response.data
        assert b"Upload Private Test" not in response.data
    assert yt.oauth == 0 and not yt.channels and not yt.inserts


def test_exact_scope_contract_and_rejecting_expansion():
    assert set(youtube.SCOPES) == {"https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"}
    for scopes in ([youtube.SCOPES[0]], youtube.SCOPES + ["https://www.googleapis.com/auth/youtube"]):
        with pytest.raises(youtube.YouTubeError):
            youtube.check_scopes(SimpleNamespace(scopes=scopes, granted_scopes=scopes))


def test_connect_verifies_handle_via_id_and_never_uploads(yt):
    connect(yt)
    status = yt.studio.status()
    assert status["channel"]["id"] == CHANNEL
    assert status["channel"]["handle"] == youtube.EXPECTED_HANDLE
    assert all(r["part"] == "snippet" for r in yt.channels)
    assert yt.channels[0]["mine"] is True
    assert yt.channels[1]["forHandle"] == youtube.EXPECTED_HANDLE
    assert not yt.inserts
    assert stat.S_IMODE(yt.studio.token().stat().st_mode) == 0o600
    assert stat.S_IMODE(yt.studio.token().parent.stat().st_mode) == 0o700
    page = yt.browser.get("/youtube", base_url=BASE)
    assert PRIVATE.encode() not in page.data
    assert PRIVATE not in yt.studio.state_path.read_text()


def test_wrong_channel_disables_all_upload_paths(yt):
    yt.wrong = True
    yt.studio.connection_action("connect", "a" * 64)
    assert yt.studio.status()["connected"] and not yt.studio.status()["verified"]
    with pytest.raises(youtube.YouTubeError, match="verify"):
        yt.studio.preview(yt.key, "kids")
    assert not yt.inserts


def test_disconnect_removes_only_token_and_invalidates_previews(yt):
    connect(yt)
    before = yt.video_path.read_bytes()
    preview = yt.studio.preview(yt.key, "kids")
    yt.studio.connection_action("disconnect", "b" * 64)
    assert not yt.studio.token().exists() and not yt.studio.status()["verified"]
    assert yt.video_path.read_bytes() == before
    with pytest.raises(youtube.YouTubeError):
        yt.studio.upload(preview["nonce"])
    assert not yt.inserts


def test_single_confirmed_upload_uses_existing_uploader_and_exact_preview(yt):
    connect(yt)
    preview = yt.studio.preview(yt.key, "kids")
    assert not yt.inserts
    yt.studio.upload(preview["nonce"])
    assert len(yt.inserts) == 1 and yt.chunks == 1
    assert yt.inserts[0]["body"] == preview["body"]
    assert yt.inserts[0]["notifySubscribers"] is False
    assert preview["body"]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": True, "containsSyntheticMedia": True}
    saved = yt.studio.upload_history()[0]
    assert saved["video_id"] == VIDEO and saved["status"] == "complete"
    assert saved["returned_privacy_status"] == "private"
    with pytest.raises(youtube.YouTubeError):
        yt.studio.upload(preview["nonce"])
    restarted = YouTubeDashboard(yt.settings, yt.app.extensions["dashboard_store"])
    with pytest.raises(youtube.YouTubeError):
        restarted.preview(yt.key, "general")
    assert len(yt.inserts) == 1


def test_set_thumbnail_uses_this_projects_own_scene_01_image(yt):
    connect(yt)
    preview = yt.studio.preview(yt.key, "kids")
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[0]
    assert not record.get("thumbnail_set_at")
    yt.studio.set_video_thumbnail(record["video_id"], "c" * 64)
    assert len(yt.thumbnails) == 1
    assert yt.thumbnails[0]["videoId"] == VIDEO
    assert yt.thumbnails[0]["media_body"]._filename.endswith("scene-01.png")
    updated = yt.studio.upload_history()[0]
    assert updated.get("thumbnail_set_at")
    with pytest.raises(youtube.YouTubeError, match="already submitted"):
        yt.studio.set_video_thumbnail(record["video_id"], "c" * 64)


def test_set_thumbnail_reports_an_unverified_channel_clearly_video_stays_unaffected(yt):
    from googleapiclient.errors import HttpError
    connect(yt)
    preview = yt.studio.preview(yt.key, "kids")
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[0]
    response = SimpleNamespace(status=403, reason="Forbidden")
    yt.thumbnail_error = HttpError(response, json.dumps(
        {"error": {"errors": [{"reason": "youtubeSignupRequired"}]}}).encode())
    with pytest.raises(youtube.YouTubeError, match="phone-verified"):
        yt.studio.set_video_thumbnail(record["video_id"], "d" * 64)
    assert yt.studio.upload_history()[0]["status"] == "complete"
    assert not yt.studio.upload_history()[0].get("thumbnail_set_at")


def test_two_previews_cannot_upload_the_same_video_twice(yt):
    connect(yt)
    a, b = yt.studio.preview(yt.key, "kids"), yt.studio.preview(yt.key, "kids")
    yt.studio.upload(a["nonce"])
    with pytest.raises(youtube.YouTubeError, match="already exists"):
        yt.studio.upload(b["nonce"])
    assert len(yt.inserts) == 1


def test_ambiguous_upload_stops_without_retry_or_secret_leak_and_survives_restart(yt):
    connect(yt)
    preview = yt.studio.preview(yt.key, "general")
    yt.fail = True
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[0]
    assert record["status"] == "unknown" and record["video_id"] is None
    assert PRIVATE not in json.dumps(record)
    assert yt.chunks == 1
    with pytest.raises(youtube.YouTubeError):
        yt.studio.preview(yt.key, "general")


@pytest.mark.parametrize("change", ["video", "story", "channel"])
def test_changed_preview_or_channel_never_uploads(yt, change):
    connect(yt)
    preview = yt.studio.preview(yt.key, "kids")
    if change == "video":
        yt.video_path.write_bytes(b"changed mock video")
    elif change == "story":
        path = yt.video_path.parent / "story.json"
        data = json.loads(path.read_text())
        data["title"] = "Changed offline title"
        save_json(path, data)
    else:
        yt.wrong = True
    with pytest.raises(youtube.YouTubeError):
        yt.studio.upload(preview["nonce"])
    assert not yt.inserts


def test_csrf_origin_and_explicit_upload_consent_are_enforced(yt):
    yt.browser.get("/youtube", base_url=BASE)
    assert yt.browser.post("/youtube/connect", base_url=BASE).status_code == 403
    with yt.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    data = {"csrf": csrf, "nonce": "a" * 64}
    assert yt.browser.post("/youtube/connect", data=data, base_url=BASE, headers={"Origin": "https://evil.test"}).status_code == 403
    assert yt.browser.post("/youtube/upload", data=data, base_url=BASE).status_code == 400
    assert yt.oauth == 0 and not yt.inserts


def test_unlisted_scheduled_and_unconfirmed_uploads_remain_disabled(yt):
    video = yt.studio.video(yt.key)
    with pytest.raises(youtube.YouTubeError):
        youtube.upload_video(yt.settings, video["story"], video["path"], privacy="unlisted")
    with pytest.raises(youtube.YouTubeError):
        youtube.upload_video(yt.settings, video["story"], video["path"])  # no confirmed_body/client: not a real dashboard upload
    assert not yt.inserts


def test_public_upload_is_accepted_when_google_honors_it(yt):
    connect(yt)
    key = add_cast_video(yt, "cast-video-public")
    preview = yt.studio.preview(key, "general", privacy="public")
    assert preview["body"]["status"]["privacyStatus"] == "public"
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    assert record["status"] == "complete"
    assert yt.inserts[-1]["body"]["status"]["privacyStatus"] == "public"
    assert record["returned_privacy_status"] == "private"  # this fixture's mock always returns private
    assert "YouTube returned private even though public was requested" in record["message"]


def test_public_upload_message_confirms_when_google_actually_honors_public(yt):
    connect(yt)

    def next_chunk(num_retries=0):
        assert num_retries == 0
        yt.chunks += 1
        return None, {"id": VIDEO, "status": {"privacyStatus": "public"}}
    yt.client.next_chunk = next_chunk
    key = add_cast_video(yt, "cast-video-public-honored")
    preview = yt.studio.preview(key, "general", privacy="public")
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    assert record["returned_privacy_status"] == "public"
    assert record["message"] == "Upload completed. YouTube confirmed public."


def test_tokens_and_json_cannot_be_served_as_media(yt):
    for path in ("/secrets/youtube_token.json", "/youtube/video/../../secrets/youtube_token.json/media"):
        response = yt.browser.get(path, base_url=BASE)
        assert response.status_code in {400, 404}
        assert PRIVATE.encode() not in response.data


def test_real_oauth_wrapper_uses_loopback_and_never_prints_authorization_url(tmp_path, monkeypatch, capsys):
    from google_auth_oauthlib.flow import InstalledAppFlow
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    credential = youtube.secret_path(settings, "youtube_client_secret")
    save_json(credential, {"installed": {"client_id": PRIVATE, "client_secret": PRIVATE,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
    captured = {}
    def flow_file(path, scopes, **kwargs):
        captured.update(scopes=scopes, flow_options=kwargs)
        def local(**options):
            captured.update(options=options)
            return SimpleNamespace(scopes=youtube.SCOPES, granted_scopes=youtube.SCOPES, to_json=lambda: PRIVATE)
        return SimpleNamespace(run_local_server=local)
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", flow_file)
    youtube.authorize(settings)
    assert captured["scopes"] == youtube.SCOPES
    assert captured["flow_options"]["autogenerate_code_verifier"] is True
    assert captured["options"]["host"] == captured["options"]["bind_addr"] == "127.0.0.1"
    assert captured["options"]["authorization_prompt_message"] is None
    assert captured["options"]["include_granted_scopes"] == "false"
    assert capsys.readouterr().out == ""


def test_web_credentials_and_unsafe_secret_paths_rejected(tmp_path):
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    path = youtube.secret_path(settings, "youtube_client_secret")
    save_json(path, {"web": {"client_secret": PRIVATE}})
    with pytest.raises(youtube.YouTubeError, match="Desktop"):
        youtube.validate_desktop_client(path)
    settings.raw["paths"]["youtube_token"] = "exposed-token.json"
    with pytest.raises(youtube.YouTubeError, match="secrets"):
        youtube.secret_path(settings, "youtube_token")


WEB_CLIENT = {"web": {"client_id": PRIVATE, "client_secret": PRIVATE,
              "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}


def test_validate_web_client_accepts_web_rejects_installed_or_malformed(tmp_path):
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    path = youtube.secret_path(settings, "youtube_client_secret")
    save_json(path, WEB_CLIENT)
    youtube.validate_web_client(path)  # must not raise
    save_json(path, {"installed": {"client_id": PRIVATE, "client_secret": PRIVATE,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
    with pytest.raises(youtube.YouTubeError, match="Web application"):
        youtube.validate_web_client(path)
    save_json(path, {"web": {"client_id": PRIVATE}})  # missing required fields
    with pytest.raises(youtube.YouTubeError, match="Web application"):
        youtube.validate_web_client(path)


def test_client_credential_kind_detects_installed_web_or_none(tmp_path):
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    assert youtube.client_credential_kind(settings) is None  # no file yet
    path = youtube.secret_path(settings, "youtube_client_secret")
    save_json(path, WEB_CLIENT)
    assert youtube.client_credential_kind(settings) == "web"
    save_json(path, {"installed": {"client_id": PRIVATE, "client_secret": PRIVATE,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
    assert youtube.client_credential_kind(settings) == "installed"


def test_web_authorization_url_and_callback_exchange_flow(tmp_path, monkeypatch):
    from google_auth_oauthlib.flow import Flow
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    save_json(youtube.secret_path(settings, "youtube_client_secret"), WEB_CLIENT)
    captured = {}

    class FakeFlow:
        def __init__(self, redirect_uri):
            self.redirect_uri = redirect_uri
            self.credentials = SimpleNamespace(scopes=youtube.SCOPES, granted_scopes=youtube.SCOPES, to_json=lambda: PRIVATE)

        def authorization_url(self, **kwargs):
            captured["auth_kwargs"] = kwargs
            return f"https://accounts.google.com/o/oauth2/v2/auth?state={kwargs['state']}", kwargs["state"]

        def fetch_token(self, authorization_response):
            captured["authorization_response"] = authorization_response

    def from_client_secrets_file(path, scopes, redirect_uri=None, state=None):
        captured["scopes"] = scopes
        return FakeFlow(redirect_uri)
    monkeypatch.setattr(Flow, "from_client_secrets_file", staticmethod(from_client_secrets_file))

    url = youtube.web_authorization_url(settings, "https://example.test/youtube/oauth2callback", "the-state")
    assert captured["scopes"] == youtube.SCOPES
    assert captured["auth_kwargs"]["state"] == "the-state"
    assert "the-state" in url

    youtube.web_authorize_callback(settings, "https://example.test/youtube/oauth2callback",
        "https://example.test/youtube/oauth2callback?code=abc&state=the-state")
    assert captured["authorization_response"].endswith("state=the-state")
    assert youtube.secret_path(settings, "youtube_token").is_file()
    # A second attempt while a token already exists must not silently overwrite it.
    with pytest.raises(youtube.YouTubeError, match="Disconnect"):
        youtube.web_authorize_callback(settings, "https://example.test/youtube/oauth2callback",
            "https://example.test/youtube/oauth2callback?code=abc&state=the-state")


def test_connect_redirects_straight_to_google_for_a_web_credential(yt, monkeypatch):
    save_json(youtube.secret_path(yt.settings, "youtube_client_secret"), WEB_CLIENT)
    monkeypatch.setattr(youtube, "web_authorization_url",
        lambda settings, redirect_uri, state: f"https://accounts.google.com/mock?state={state}&redirect_uri={redirect_uri}")
    yt.browser.get("/youtube", base_url=BASE)
    with yt.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    response = yt.browser.post("/youtube/connect", base_url=BASE, data={"csrf": csrf, "nonce": "e" * 64})
    assert response.status_code == 302
    assert response.location.startswith("https://accounts.google.com/mock?")
    assert f"redirect_uri=https://{BASE.split('://')[1]}/youtube/oauth2callback" in response.location
    assert yt.oauth == 0  # the local-machine flow was never touched


def test_oauth2callback_completes_the_connection_for_a_web_credential(yt, monkeypatch):
    save_json(youtube.secret_path(yt.settings, "youtube_client_secret"), WEB_CLIENT)
    def fake_authorize_callback(settings, redirect_uri, authorization_response):
        youtube.save_token(youtube.secret_path(settings, "youtube_token"), PRIVATE)
        return str(youtube.secret_path(settings, "youtube_token"))
    monkeypatch.setattr(youtube, "web_authorize_callback", fake_authorize_callback)
    # web_authorization_url runs for real here (it only builds a URL locally, no network
    # call), which is what actually issues and saves the state this callback must present back.
    yt.studio.web_oauth_start("https://example.test/youtube/oauth2callback", "f" * 64)
    real_state = json.loads((yt.studio.directory / "oauth-state.json").read_text())["state"]
    response = yt.browser.get(f"/youtube/oauth2callback?code=abc&state={real_state}", base_url=BASE)
    assert response.status_code == 303
    assert yt.studio.status()["verified"]


def test_oauth2callback_rejects_a_state_mismatch_without_crashing(yt):
    save_json(youtube.secret_path(yt.settings, "youtube_client_secret"), WEB_CLIENT)
    response = yt.browser.get("/youtube/oauth2callback?code=abc&state=not-the-real-state", base_url=BASE)
    assert response.status_code == 303
    assert not yt.studio.status()["connected"]
    page = yt.browser.get("/youtube", base_url=BASE)
    assert b"could not be verified" in page.data


def test_oauth2callback_reports_a_google_side_error_without_crashing(yt):
    save_json(youtube.secret_path(yt.settings, "youtube_client_secret"), WEB_CLIENT)
    response = yt.browser.get("/youtube/oauth2callback?error=access_denied", base_url=BASE)
    assert response.status_code == 303
    page = yt.browser.get("/youtube", base_url=BASE)
    assert b"access_denied" in page.data
    assert not yt.studio.status()["connected"]


def test_connection_post_is_explicit_and_duplicate_submission_never_reopens_google(yt):
    yt.browser.get("/youtube", base_url=BASE)
    with yt.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    data = {"csrf": csrf, "nonce": "c" * 64}
    assert yt.browser.post("/youtube/connect", data=data, base_url=BASE).status_code == 303
    assert yt.oauth == 1 and not yt.inserts
    assert yt.browser.post("/youtube/connect", data=data, base_url=BASE).status_code == 400
    assert yt.oauth == 1
    page = yt.browser.get("/youtube", base_url=BASE)
    assert CHANNEL.encode() in page.data and PRIVATE.encode() not in page.data


def test_replaced_token_invalidates_cached_channel(yt):
    connect(yt)
    youtube.save_token(yt.studio.token(), "different-private-token")
    assert not yt.studio.status()["verified"]
    with pytest.raises(youtube.YouTubeError):
        yt.studio.preview(yt.key, "kids")


def test_interrupted_upload_record_is_not_restarted(yt):
    connect(yt)
    preview = yt.studio.preview(yt.key, "kids")
    path = yt.studio.directory / "uploads" / (preview["video_sha256"] + ".json")
    save_json(path, {**preview, "status": "in_progress", "video_id": None})
    restarted = YouTubeDashboard(yt.settings, yt.app.extensions["dashboard_store"])
    with pytest.raises(youtube.YouTubeError):
        restarted.preview(yt.key, "kids")
    assert not yt.inserts


def test_public_figure_disclosure_is_identical_in_preview_and_upload_body(yt):
    from elsewhere.disclosure import DISCLOSURE
    connect(yt)
    path = yt.video_path.parent / "story.json"
    data = json.loads(path.read_text())
    data["youtube_disclosure"] = DISCLOSURE
    save_json(path, data)
    preview = yt.studio.preview(yt.key, "kids")
    assert preview["body"]["snippet"]["description"].count(DISCLOSURE) == 1
    yt.studio.upload(preview["nonce"])
    assert yt.inserts[0]["body"]["snippet"]["description"] == preview["body"]["snippet"]["description"]


def test_failed_test_connection_invalidates_old_verification_without_leaking_errors(yt, monkeypatch):
    connect(yt)
    def fail(_):
        raise ValueError(PRIVATE)
    monkeypatch.setattr(youtube, "youtube_client", fail)
    yt.studio.connection_action("test", "d" * 64)
    assert not yt.studio.status()["verified"]
    assert PRIVATE not in json.dumps(yt.studio.status())
    assert not yt.inserts


def test_upload_requires_explicit_audience_and_no_scheduled_publish(yt):
    from datetime import UTC, datetime
    connect(yt)
    with pytest.raises(youtube.YouTubeError):
        yt.studio.preview(yt.key, "")
    v = yt.studio.video(yt.key)
    with pytest.raises(youtube.YouTubeError):
        youtube.upload_video(yt.settings, v["story"], v["path"], publish_at=datetime.now(UTC))
    assert not yt.inserts


def add_cast_video(yt, name, *, title="The Cup With Two Paths", disclosure_text=None):
    """A locally validated output whose story carries the real backend Messi/Ronaldo disclosure."""
    from elsewhere.disclosure import DISCLOSURE
    folder = yt.settings.path("output") / name
    folder.mkdir()
    (folder / "final.mp4").write_bytes(f"MOCK CAST VIDEO {name}".encode())
    payload = json.loads((ROOT / "stories/wedding-photo-test.json").read_text())
    payload.update(title=title, youtube_disclosure=disclosure_text or DISCLOSURE, story_category="Emotional",
                   description="Messi and Ronaldo restore a cracked clay cup together.")
    save_json(folder / "story.json", payload)
    save_json(folder / "validation.json", {"checks": {"mock_offline": True}})
    return next(v["key"] for v in yt.studio.videos() if v["path"] == folder / "final.mp4")


def test_generated_title_follows_the_permanent_contract_and_blocks_a_duplicate(yt):
    connect(yt)
    key_a = add_cast_video(yt, "cast-video-a")
    key_b = add_cast_video(yt, "cast-video-b")
    preview = yt.studio.preview(key_a, "general")
    assert preview["body"]["snippet"]["title"] == "Messi and Ronaldo The Cup With Two Paths | Emotional Story #Shorts"
    assert set(yt.settings.publishing["tags"]) <= set(preview["body"]["snippet"]["tags"])
    assert "emotional story" in preview["body"]["snippet"]["tags"]
    yt.studio.upload(preview["nonce"])
    assert yt.studio.upload_history()[-1]["status"] == "complete"
    with pytest.raises(youtube.YouTubeError, match="already used"):
        yt.studio.preview(key_b, "general")


def test_edit_metadata_overrides_reach_the_upload_and_privacy_status_is_recorded(yt):
    connect(yt)
    key = add_cast_video(yt, "cast-video-edit")
    preview = yt.studio.preview(key, "general", title_hook="Built A Secret Workshop",
        description_summary="A hand-edited summary about the workshop.", extra_tags=["clay art"])
    assert preview["body"]["snippet"]["title"] == "Messi and Ronaldo Built A Secret Workshop | Emotional Story #Shorts"
    assert preview["body"]["snippet"]["description"].startswith("A hand-edited summary about the workshop.")
    assert "clay art" in preview["body"]["snippet"]["tags"]
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    assert record["status"] == "complete" and record["returned_privacy_status"] == "private"
    assert yt.inserts[-1]["body"]["snippet"]["title"] == "Messi and Ronaldo Built A Secret Workshop | Emotional Story #Shorts"


def test_recomputed_metadata_for_an_existing_upload_is_display_only(yt):
    connect(yt)
    key = add_cast_video(yt, "cast-video-recompute")
    preview = yt.studio.preview(key, "general")
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    current = yt.studio.recomputed_metadata(record)
    assert current["snippet"]["title"] == "Messi and Ronaldo The Cup With Two Paths | Emotional Story #Shorts"
    assert len(yt.inserts) == 1  # recomputing never uploads again
    assert yt.studio.studio_url(record["video_id"]) == f"https://studio.youtube.com/video/{record['video_id']}/edit"


def test_recomputed_metadata_upgrades_an_older_wording_of_the_current_four_cast_disclosure(yt):
    # A previous phrasing of the SAME four-cast disclosure must still be recognized as a cast
    # story, and recompute must show today's exact wording rather than the stale one on disk.
    older_four_cast_wording = ("This is unofficial fiction for entertainment. Not endorsed by "
        "Lionel Messi, Cristiano Ronaldo, IShowSpeed or MrBeast, their clubs, channels or sponsors.")
    connect(yt)
    key = add_cast_video(yt, "cast-video-old-disclosure", disclosure_text=older_four_cast_wording)
    preview = yt.studio.preview(key, "general")
    assert preview["body"]["snippet"]["title"] == "Messi and Ronaldo The Cup With Two Paths | Emotional Story #Shorts"
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    from elsewhere.disclosure import DISCLOSURE
    current = yt.studio.recomputed_metadata(record)
    assert current["snippet"]["description"].count(DISCLOSURE) == 1
    assert older_four_cast_wording not in current["snippet"]["description"]


def test_a_genuine_two_person_historical_video_is_never_relabeled_as_the_full_cast(yt):
    # A video that only ever featured Messi and Ronaldo must not be dishonestly claimed to
    # feature IShowSpeed and MrBeast too, just because the permanent cast later grew to four.
    old_two_person_disclosure = ("This is an unofficial, fictional AI-generated story created for entertainment. "
                       "It is not affiliated with or endorsed by Lionel Messi, Cristiano Ronaldo, their clubs, sponsors, or representatives.")
    connect(yt)
    key = add_cast_video(yt, "cast-video-two-person-only", disclosure_text=old_two_person_disclosure)
    preview = yt.studio.preview(key, "general")
    assert preview["body"]["snippet"]["title"] == "The Cup With Two Paths | Emotional Story #Shorts"
    assert "IShowSpeed" not in preview["body"]["snippet"]["title"]


def test_check_live_status_reflects_a_manual_change_made_outside_the_app(yt):
    connect(yt)
    key = add_cast_video(yt, "cast-video-status")
    preview = yt.studio.preview(key, "general")
    yt.studio.upload(preview["nonce"])
    record = yt.studio.upload_history()[-1]
    assert record["video_id"] == VIDEO
    assert "live_privacy_status" not in record or record["live_privacy_status"] is None
    # Someone changed the video to Public by hand in YouTube Studio, outside this app.
    yt.live_status = "public"
    status = yt.studio.check_live_status(VIDEO, "e" * 64)
    assert status == "public"
    assert yt.status_checks and yt.status_checks[-1] == {"part": "status", "id": VIDEO, "maxResults": 1}
    refreshed = yt.studio.upload_history()[-1]
    assert refreshed["live_privacy_status"] == "public" and refreshed["live_status_checked_at"]
    assert not yt.inserts[1:]  # the read-only check never uploads


def test_check_live_status_rejects_bad_ids_and_unknown_videos(yt):
    connect(yt)
    with pytest.raises(youtube.YouTubeError):
        yt.studio.check_live_status("not-a-real-id", "1" * 64)
    with pytest.raises(youtube.YouTubeError):
        yt.studio.check_live_status(VIDEO, "2" * 64)  # no saved upload record has this video ID yet


def test_check_status_route_updates_the_page_and_still_enforces_csrf(yt):
    connect(yt)
    key = add_cast_video(yt, "cast-video-status-route")
    preview = yt.studio.preview(key, "general")
    yt.studio.upload(preview["nonce"])
    yt.live_status = "public"
    page = yt.browser.get("/youtube", base_url=BASE)
    assert b"Check current status on YouTube" in page.data
    with yt.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    data = {"csrf": csrf, "nonce": "f" * 64}
    response = yt.browser.post(f"/youtube/upload/{VIDEO}/check-status", data=data, base_url=BASE)
    assert response.status_code == 303
    page = yt.browser.get("/youtube", base_url=BASE)
    assert b"public" in page.data and b"never assumes the requested value was honored" in page.data
