"""Manual local YouTube actions; no startup network calls, workers or upload queue."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, send_file

from . import youtube
from .config import Settings
from .costs import utc_now
from .disclosure import DISCLOSURE, is_recurring_cast_story
from .models import StoryPackage
from .openai_service import save_json


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else (default or {})


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def best_known_privacy(record):
    """The most trustworthy privacy value available: a fresh live check beats what Google
    returned at upload time, which beats what was merely requested. Shared by the connection
    page and the Control Center so both never disagree about a video's actual status."""
    return (record.get("live_privacy_status") or record.get("returned_privacy_status")
            or record.get("body", {}).get("status", {}).get("privacyStatus"))


class YouTubeDashboard:
    def __init__(self, settings, manager):
        self.settings, self.manager = settings, manager
        self.directory = settings.root / "data/youtube"
        if self.directory.is_symlink():
            raise youtube.YouTubeError("Unsafe YouTube state directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in ("actions", "previews", "uploads"):
            (self.directory / name).mkdir(exist_ok=True)
        self.state_path = self.directory / "connection.json"

    def token(self):
        return youtube.secret_path(self.settings, "youtube_token")

    def status(self):
        state = read(self.state_path)
        token = self.token()
        present = token.is_file()
        verified = (present and state.get("channel", {}).get("matches_expected") is True
                    and state.get("credential_hash") == digest_file(token))
        return {"connected": present, "verified": verified, "channel": state.get("channel", {}) if present else {},
                "message": state.get("message", "Not connected. No upload occurs during connection."),
                "checked_at": state.get("checked_at"), "expected_handle": youtube.EXPECTED_HANDLE,
                "credential_ready": self.settings.path("youtube_client_secret").is_file(),
                "scopes": youtube.SCOPES}

    def save_connection(self, client):
        channel = youtube.identify_channel(client)
        save_json(self.state_path, {"channel": channel, "checked_at": utc_now(),
            "credential_hash": digest_file(self.token()),
            "message": "Connected channel verified. Uploads require a separate Private test confirmation."
            if channel["matches_expected"] else "Wrong channel. Disconnect and select One Minute Elsewhere when reconnecting. Uploads are locked."})
        return channel

    @contextmanager
    def action(self, nonce):
        if not re.fullmatch(r"[a-f0-9]{64}", nonce or ""):
            raise youtube.YouTubeError("This form expired. Refresh the page.")
        with (self.directory / "action.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise youtube.YouTubeError("A YouTube action is already running. Wait for it to finish.") from None
            try:
                marker = self.directory / "actions" / (nonce + ".json")
                try:
                    with marker.open("x") as stream:
                        json.dump({"claimed_at": utc_now()}, stream)
                except FileExistsError:
                    raise youtube.YouTubeError("This action was already submitted. Refresh to see its saved result.") from None
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def connection_action(self, action, nonce):
        with self.action(nonce):
            if action == "disconnect":
                self.token().unlink(missing_ok=True)
                save_json(self.state_path, {"message": "Disconnected locally; the saved token was deleted. Google's account grant was not revoked. No upload occurred."})
                return
            if action not in {"connect", "test"}:
                raise youtube.YouTubeError("Unknown YouTube action")
            # Invalidate any old verified badge before a request that might fail.
            save_json(self.state_path, {"message": "Connecting or checking the channel. No upload occurs during this action."})
            try:
                if action == "connect":
                    youtube.authorize(self.settings)
                self.save_connection(youtube.youtube_client(self.settings))
            except Exception as error:  # noqa: BLE001 - only app-owned errors reach the browser
                message = str(error) if isinstance(error, youtube.YouTubeError) else "Google connection could not be verified. No upload occurred. Check the API setup and use Test connection or reconnect."
                save_json(self.state_path, {"message": message})

    def videos(self):
        """Resolve only completed pipeline artifacts, including preserved CLI videos."""
        result = []
        output = self.manager.output
        for folder in sorted(output.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            project = read(folder / "dashboard-project.json")
            if project:
                if project.get("status") != "complete":
                    continue
                directory = self.manager.revision_dir(project)
            else:
                directory = folder
            video, story_path, validation_path = [directory / name for name in ("final.mp4", "story.json", "validation.json")]
            if any(p.is_symlink() or not p.is_file() for p in (video, story_path, validation_path)):
                continue
            if not directory.resolve().is_relative_to(output.resolve()):
                continue
            checks = read(validation_path).get("checks", {})
            if not checks or not all(v is True for v in checks.values()):
                continue
            try:
                story = StoryPackage.model_validate_json(story_path.read_text())
            except (ValueError, OSError):
                continue
            key = hashlib.sha256(str(video.relative_to(output)).encode()).hexdigest()[:32]
            code = (project.get("language", "en") if project else
                    read(directory / "settings-snapshot.json").get("brand", {}).get("language", self.settings.brand["language"]))
            result.append({"key": key, "title": story.title, "path": video, "story": story,
                           "language": code,
                           "video_mode": (project.get("video_mode") if project else read(directory / "settings-snapshot.json").get("dashboard_brief", {}).get("video_mode")),
                           "directory": str(directory), "validation": read(validation_path)})
        return result

    def video_settings(self, video):
        raw = copy.deepcopy(self.settings.raw)
        raw["brand"]["language"] = video.get("language", self.settings.brand["language"])
        if video.get("video_mode") == "viral":
            raw.setdefault("dashboard_brief", {})["video_mode"] = "viral"
        return Settings(self.settings.root, raw)

    def video(self, key):
        for video in self.videos():
            if video["key"] == key:
                return video
        raise youtube.YouTubeError("That video is not a completed, validated local output")

    def previous_titles(self):
        return {record["body"]["snippet"]["title"] for record in self.upload_history()
                if record.get("body", {}).get("snippet", {}).get("title")}

    def preview(self, key, audience, *, privacy="private", title_hook=None, description_summary=None, extra_tags=None):
        if not self.status()["verified"]:
            raise youtube.YouTubeError("Connect and verify @OneMinuteElsewhere1 before preparing an upload")
        if audience not in {"kids", "general"}:
            raise youtube.YouTubeError("Choose the audience setting")
        if privacy not in {"private", "public"}:
            raise youtube.YouTubeError("Choose Private or Public before uploading")
        video = self.video(key)
        fingerprint = digest_file(video["path"])
        if (self.directory / "uploads" / (fingerprint + ".json")).exists():
            raise youtube.YouTubeError("This exact video already has an upload attempt. Check the saved result; duplicate uploads are blocked.")
        overrides = {k: v for k, v in {"title_hook": title_hook, "description_summary": description_summary,
                                        "extra_tags": extra_tags}.items() if v}
        body = youtube.upload_body(self.video_settings(video), video["story"], audience == "kids", privacy=privacy, **overrides)
        if body["snippet"]["title"] in self.previous_titles():
            raise youtube.YouTubeError("This exact YouTube title was already used for a previous upload. Edit the title before continuing.")
        nonce = secrets.token_hex(32)
        preview = {"key": key, "nonce": nonce, "created_at": utc_now(), "video_sha256": fingerprint,
                   "path": str(video["path"]), "channel": self.status()["channel"],
                   "overrides": overrides, "body": body}
        save_json(self.directory / "previews" / (nonce + ".json"), preview)
        return preview

    def upload(self, nonce):
        with self.action(nonce):
            preview = read(self.directory / "previews" / (nonce + ".json"))
            if not preview or not self.status()["verified"]:
                raise youtube.YouTubeError("Review the exact video and verify the channel before uploading")
            video = self.video(preview["key"])
            if digest_file(video["path"]) != preview["video_sha256"]:
                raise youtube.YouTubeError("The video changed after preview. Review it again; nothing uploaded.")
            audience = preview["body"]["status"]["selfDeclaredMadeForKids"]
            privacy = preview["body"]["status"]["privacyStatus"]
            overrides = preview.get("overrides") or {}
            if youtube.upload_body(self.video_settings(video), video["story"], audience, privacy=privacy, **overrides) != preview["body"]:
                raise youtube.YouTubeError("The metadata changed after preview. Review it again; nothing uploaded.")
            try:
                client = youtube.youtube_client(self.settings)
                channel = self.save_connection(client)
            except Exception:  # noqa: BLE001 - no raw Google payloads in errors
                save_json(self.state_path, {"message": "Channel re-verification failed. Upload did not start; use Test connection."})
                raise youtube.YouTubeError("Channel re-verification failed; nothing uploaded") from None
            if not channel["matches_expected"] or channel["id"] != preview["channel"]["id"]:
                raise youtube.YouTubeError("Destination channel changed or is wrong; nothing uploaded")
            record_path = self.directory / "uploads" / (preview["video_sha256"] + ".json")
            record = {**preview, "status": "in_progress", "started_at": utc_now(), "video_id": None,
                      "returned_privacy_status": None,
                      "automatic_retries": 0, "message": "Upload may be in progress. Do not start a second attempt."}
            try:
                with record_path.open("x") as handle:
                    json.dump(record, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                raise youtube.YouTubeError("An upload attempt already exists for this video; it will not be repeated") from None
            try:
                result = youtube.upload_video(self.video_settings(video), video["story"], video["path"], privacy=privacy,
                    client=client, confirmed_body=preview["body"], confirmed_overrides=overrides, verified_channel_id=channel["id"])
                honored = result["privacy_status"] == privacy
                message = (f"Upload completed. YouTube confirmed {result['privacy_status']}." if honored else
                    f"Upload completed, but YouTube returned {result['privacy_status']} even though {privacy} was requested. "
                    "Check 'Check current status on YouTube' for the live value.")
                record.update(status="complete", video_id=result["video_id"], returned_privacy_status=result["privacy_status"],
                              completed_at=utc_now(), message=message)
            except Exception:  # noqa: BLE001 - preserve uncertain outcome without exposing response data
                record.update(status="unknown", completed_at=utc_now(),
                              message="Upload outcome is uncertain. Check YouTube Studio before taking any further action. No automatic retry.")
            save_json(record_path, record)

    def upload_history(self):
        return [read(path) for path in sorted((self.directory / "uploads").glob("*.json"))]

    def studio_url(self, video_id):
        return f"https://studio.youtube.com/video/{video_id}/edit"

    def check_live_status(self, video_id, nonce):
        """On-demand, read-only refresh of a video's CURRENT status straight from YouTube.

        A video uploaded Private can later be changed manually in YouTube Studio (outside
        this API project); the cached submitted/returned status never reflects that. This
        never edits anything and never requests a broader scope than youtube.readonly.
        """
        with self.action(nonce):
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
                raise youtube.YouTubeError("That is not a valid YouTube video ID")
            record_path = next((path for path in (self.directory / "uploads").glob("*.json")
                                if read(path).get("video_id") == video_id), None)
            if record_path is None:
                raise youtube.YouTubeError("No saved upload attempt matches that video ID")
            client = youtube.youtube_client(self.settings)
            status = youtube.video_status(client, video_id)
            record = read(record_path)
            record.update(live_privacy_status=status, live_status_checked_at=utc_now())
            save_json(record_path, record)
            return status

    def set_video_thumbnail(self, video_id, nonce):
        """Best-effort: sets this project's own scene-01 image (prompted to be the
        clickbait-worthy thumbnail candidate) as the video's custom YouTube thumbnail.
        Needs a phone-verified channel; never repeats automatically on failure."""
        with self.action(nonce):
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
                raise youtube.YouTubeError("That is not a valid YouTube video ID")
            record_path = next((path for path in (self.directory / "uploads").glob("*.json")
                                if read(path).get("video_id") == video_id), None)
            if record_path is None:
                raise youtube.YouTubeError("No saved upload attempt matches that video ID")
            record = read(record_path)
            image = Path(record["path"]).parent / "scene-01.png"
            client = youtube.youtube_client(self.settings)
            youtube.set_thumbnail(client, video_id, image)
            record.update(thumbnail_set_at=utc_now())
            save_json(record_path, record)

    def recomputed_metadata(self, record):
        """What today's metadata rules would produce for an already-uploaded video.

        Never sent to Google: display-only, for manual copying into YouTube Studio,
        since editing an existing video is out of scope without broader OAuth access.
        """
        made_for_kids = record.get("body", {}).get("status", {}).get("selfDeclaredMadeForKids")
        if type(made_for_kids) is not bool:
            return None
        try:
            video = self.video(record["key"])
            story = video["story"]
        except youtube.YouTubeError:
            return None
        # Always show today's fixed disclosure wording, even for a story generated
        # under an earlier revision of that permanent, backend-owned text.
        if is_recurring_cast_story(story) and story.youtube_disclosure != DISCLOSURE:
            story = story.model_copy(update={"youtube_disclosure": DISCLOSURE})
        privacy = record.get("body", {}).get("status", {}).get("privacyStatus", "private")
        return youtube.upload_body(self.video_settings(video), story, made_for_kids, privacy=privacy)


def register_youtube(app, settings, manager):
    studio = YouTubeDashboard(settings, manager)
    app.extensions["youtube_dashboard"] = studio
    routes = Blueprint("youtube", __name__)

    @routes.get("/youtube")
    def connection():
        uploads = studio.upload_history()
        status_nonces = {record["video_id"]: secrets.token_hex(32) for record in uploads if record.get("video_id")}
        thumbnail_nonces = {record["video_id"]: secrets.token_hex(32) for record in uploads if record.get("video_id")}
        return render_template("youtube.html", y=studio.status(), videos=studio.videos(),
                               uploads=uploads, nonce=secrets.token_hex(32), status_nonces=status_nonces,
                               thumbnail_nonces=thumbnail_nonces,
                               recompute=studio.recomputed_metadata, studio_url=studio.studio_url,
                               best_known_privacy=best_known_privacy)

    @routes.post("/youtube/<action>")
    def connection_action(action):
        if action not in {"connect", "disconnect", "test"}:
            abort(404)
        studio.connection_action(action, request.form.get("nonce", ""))
        return redirect("/youtube", code=303)

    @routes.get("/youtube/video/<key>")
    def preview_video(key):
        video = studio.video(key)
        audience = request.args.get("audience", "")
        privacy = request.args.get("privacy", "")
        title_hook = request.args.get("title_hook", "").strip()
        description_summary = request.args.get("description_summary", "").strip()
        extra_tags_raw = request.args.get("extra_tags", "").strip()
        extra_tags = [t.strip() for t in extra_tags_raw.split(",") if t.strip()]
        preview = studio.preview(key, audience, privacy=privacy, title_hook=title_hook or None,
            description_summary=description_summary or None, extra_tags=extra_tags or None) if audience and privacy else None
        return render_template("youtube_upload.html", y=studio.status(), video=video, preview=preview,
                               audience=audience, privacy=privacy, title_hook=title_hook,
                               description_summary=description_summary, extra_tags=extra_tags_raw)

    @routes.get("/youtube/video/<key>/media")
    def video_media(key):
        return send_file(studio.video(key)["path"], conditional=True)

    @routes.post("/youtube/upload")
    def do_upload():
        if request.form.get("confirm_upload") != "yes":
            raise youtube.YouTubeError("Confirm the exact upload first")
        studio.upload(request.form.get("nonce", ""))
        return redirect("/youtube", code=303)

    @routes.post("/youtube/upload/<video_id>/check-status")
    def check_status(video_id):
        studio.check_live_status(video_id, request.form.get("nonce", ""))
        return redirect("/youtube", code=303)

    @routes.post("/youtube/upload/<video_id>/set-thumbnail")
    def set_thumbnail(video_id):
        studio.set_video_thumbnail(video_id, request.form.get("nonce", ""))
        return redirect("/youtube", code=303)

    @app.errorhandler(youtube.YouTubeError)
    def friendly_youtube_error(error):
        return render_template("error.html", message=str(error)), 400

    app.register_blueprint(routes)
