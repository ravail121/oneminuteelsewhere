from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .config import Settings
from .disclosure import (
    combined_hashtags,
    detected_cast,
    is_recurring_cast_story,
    strip_disclosure,
    viral_hashtags,
    viral_tags,
    youtube_tags,
    youtube_title,
)
from .models import StoryPackage
from .upload_titles import roman_upload_title

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
EXPECTED_HANDLE = "@OneMinuteElsewhere1"


class YouTubeError(RuntimeError):
    """Only application-owned messages, never raw Google payloads."""


def secret_path(settings, name):
    folder = settings.root / "secrets"
    if folder.is_symlink():
        raise YouTubeError("Unsafe secrets directory")
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    path = settings.path(name)
    if path.parent != folder or path.is_symlink():
        raise YouTubeError("OAuth credentials must stay directly inside the local secrets directory")
    if path.exists():
        path.chmod(0o600)
    return path


def save_token(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_desktop_client(path):
    try:
        data = json.loads(path.read_text())
        installed = data.get("installed")
        valid = (isinstance(installed, dict) and "web" not in data
                 and all(isinstance(installed.get(k), str) and installed[k]
                         for k in ("client_id", "client_secret", "auth_uri", "token_uri"))
                 and installed["auth_uri"] in {"https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"}
                 and installed["token_uri"] in {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"})
    except (OSError, ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise YouTubeError("The local credential is not a valid Google Desktop installed client")


def validate_web_client(path):
    try:
        data = json.loads(path.read_text())
        web = data.get("web")
        valid = (isinstance(web, dict) and "installed" not in data
                 and all(isinstance(web.get(k), str) and web[k] for k in ("client_id", "client_secret", "auth_uri", "token_uri"))
                 and web["auth_uri"] in {"https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"}
                 and web["token_uri"] in {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"})
    except (OSError, ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise YouTubeError("The local credential is not a valid Google Web application client")


def client_credential_kind(settings) -> str | None:
    """'installed' (Desktop app credential; the local-machine OAuth flow below) or 'web' (Web
    application credential; the browser-redirect flow used for a remotely-reachable
    deployment) — detected from the actual downloaded Google credential JSON, never guessed,
    so the flow that actually matches the configured credential is always used."""
    try:
        data = json.loads(secret_path(settings, "youtube_client_secret").read_text())
    except (OSError, ValueError, YouTubeError):
        return None
    if "web" in data:
        return "web"
    if "installed" in data:
        return "installed"
    return None


def check_scopes(credentials):
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    if granted != set(SCOPES):
        raise YouTubeError("Google must grant exactly youtube.upload and youtube.readonly. No broader scopes are accepted; reconnect with those two permissions.")


def authorize(settings: Settings) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = secret_path(settings, "youtube_client_secret")
    token = secret_path(settings, "youtube_token")
    validate_desktop_client(secret)
    if token.exists():
        raise YouTubeError("Disconnect the existing account before connecting again")
    # OAuth libraries can log callback codes/authorization URLs at debug level.
    # Suppress logging only while this explicit interactive credential operation runs.
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES, autogenerate_code_verifier=True)
        credentials = flow.run_local_server(host="127.0.0.1", bind_addr="127.0.0.1", port=0,
            access_type="offline", prompt="consent select_account", include_granted_scopes="false",
            authorization_prompt_message=None, timeout_seconds=180,
            success_message="Authorization received. Return to the local studio for channel verification. Nothing has been uploaded.")
        check_scopes(credentials)
        save_token(token, credentials.to_json())
    except YouTubeError:
        raise
    except Exception:  # noqa: BLE001 - never expose OAuth callback/token payloads
        raise YouTubeError("Google connection did not complete. It may have been cancelled, timed out, or refused. No upload occurred. Try Connect again.") from None
    finally:
        logging.disable(previous_logging)
    return str(token)


def web_authorization_url(settings: Settings, redirect_uri: str, state: str) -> tuple[str, str]:
    """Starts the browser-redirect OAuth flow for a Web application credential: the browser
    goes to Google directly, no local listener involved, so this works for a server the
    browser cannot reach a loopback port on (a remote deployment, unlike authorize() above).
    Returns (url, code_verifier) — the PKCE code_verifier is generated fresh per call and must
    be persisted by the caller and passed back into web_authorize_callback, since that runs in
    a separate later request with a brand new Flow object that has no memory of this one."""
    from google_auth_oauthlib.flow import Flow

    secret = secret_path(settings, "youtube_client_secret")
    validate_web_client(secret)
    if secret_path(settings, "youtube_token").exists():
        raise YouTubeError("Disconnect the existing account before connecting again")
    flow = Flow.from_client_secrets_file(str(secret), SCOPES, redirect_uri=redirect_uri)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent select_account",
                                    include_granted_scopes="false", state=state)
    return url, flow.code_verifier


def web_authorize_callback(settings: Settings, redirect_uri: str, authorization_response: str,
                            code_verifier: str) -> str:
    """Completes the browser-redirect flow: exchanges the code Google just sent back (in the
    full callback URL) for tokens. The caller must already have verified the OAuth 'state'
    parameter matches what was issued in web_authorization_url before calling this, and must
    pass the same code_verifier that web_authorization_url returned for that attempt."""
    from google_auth_oauthlib.flow import Flow

    secret = secret_path(settings, "youtube_client_secret")
    validate_web_client(secret)
    token = secret_path(settings, "youtube_token")
    if token.exists():
        raise YouTubeError("Disconnect the existing account before connecting again")
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        flow = Flow.from_client_secrets_file(str(secret), SCOPES, redirect_uri=redirect_uri,
                                             code_verifier=code_verifier)
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        check_scopes(credentials)
        save_token(token, credentials.to_json())
    except YouTubeError:
        raise
    except Exception:  # noqa: BLE001 - never expose OAuth callback/token payloads
        raise YouTubeError("Google connection did not complete. It may have been cancelled, timed out, or refused. No upload occurred. Try Connect again.") from None
    finally:
        logging.disable(previous_logging)
    return str(token)


def youtube_client(settings: Settings):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = secret_path(settings, "youtube_token")
    if not token.exists():
        raise YouTubeError("YouTube is not connected. Use Connect YouTube in the dashboard.")
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        credentials = Credentials.from_authorized_user_file(str(token))
        check_scopes(credentials)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            check_scopes(credentials)
            save_token(token, credentials.to_json())
        return build("youtube", "v3", credentials=credentials, cache_discovery=False, num_retries=0)
    except YouTubeError:
        raise
    except Exception:  # noqa: BLE001 - never surface refresh-token or SDK payloads
        raise YouTubeError("The saved Google connection could not be loaded or refreshed. Use Test connection or reconnect.") from None
    finally:
        logging.disable(previous_logging)


def identify_channel(client):
    """Read-only scope is used ONLY by these channel identity requests."""
    try:
        mine = client.channels().list(part="snippet", mine=True, maxResults=50).execute(num_retries=0).get("items", [])
        expected = client.channels().list(part="snippet", forHandle=EXPECTED_HANDLE, maxResults=1).execute(num_retries=0).get("items", [])
    except Exception:  # noqa: BLE001 - provider messages may contain credential details
        raise YouTubeError("Channel verification failed. Check that YouTube Data API v3 is enabled and both approved permissions were granted. No extra scope was requested; uploads remain locked.") from None
    if len(mine) != 1:
        raise YouTubeError("Google did not identify exactly one channel. Disconnect and select One Minute Elsewhere when connecting again.")
    channel = mine[0]
    identifier = channel.get("id", "")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", identifier):
        raise YouTubeError("Google returned an invalid channel identity; uploads remain locked")
    matches = len(expected) == 1 and identifier == expected[0].get("id")
    return {"id": identifier, "name": str(channel.get("snippet", {}).get("title", "")),
            "handle": EXPECTED_HANDLE if matches else str(channel.get("snippet", {}).get("customUrl", "Unavailable")),
            "matches_expected": matches}


def video_status(client, video_id):
    """Read-only lookup of a video's CURRENT privacy status. Uses only the already-granted
    youtube.readonly scope; never edits anything, so no broader permission is needed."""
    try:
        items = client.videos().list(part="status", id=video_id, maxResults=1).execute(num_retries=0).get("items", [])
    except Exception:  # noqa: BLE001 - provider messages may contain credential details
        raise YouTubeError("Could not check the video's current status on YouTube. Try again or check YouTube Studio.") from None
    if len(items) != 1:
        raise YouTubeError("YouTube did not return that video. It may have been deleted, or the ID is wrong.")
    status = items[0].get("status", {}).get("privacyStatus")
    if not isinstance(status, str) or not status:
        raise YouTubeError("YouTube did not return a recognizable privacy status.")
    return status


def channel_statistics(client):
    """Read-only channel statistics for the connected channel. Uses only the already-granted
    youtube.readonly scope; never edits anything, so no broader permission is needed."""
    try:
        items = client.channels().list(part="statistics", mine=True, maxResults=1).execute(num_retries=0).get("items", [])
    except Exception:  # noqa: BLE001 - provider messages may contain credential details
        raise YouTubeError("Could not fetch channel statistics from YouTube.") from None
    if len(items) != 1:
        raise YouTubeError("Google did not identify exactly one channel for statistics.")
    stats = items[0].get("statistics", {})

    def count(key):
        try:
            return int(stats[key])
        except (KeyError, TypeError, ValueError):
            return None
    hidden = bool(stats.get("hiddenSubscriberCount"))
    return {"subscriber_count": None if hidden else count("subscriberCount"), "subscriber_count_hidden": hidden,
            "view_count": count("viewCount"), "video_count": count("videoCount")}


def video_statistics(client, video_ids):
    """Read-only per-video view/like/comment counts, batched in groups of 50 (the API's own
    per-call limit). Uses only the already-granted youtube.readonly scope."""
    video_ids = [v for v in dict.fromkeys(video_ids) if v]
    result = {}
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]
        try:
            items = client.videos().list(part="statistics", id=",".join(batch), maxResults=50).execute(num_retries=0).get("items", [])
        except Exception:  # noqa: BLE001 - provider messages may contain credential details
            raise YouTubeError("Could not fetch video statistics from YouTube.") from None
        for item in items:
            identifier = item.get("id")
            stats = item.get("statistics", {})

            def count(key, stats=stats):
                try:
                    return int(stats[key])
                except (KeyError, TypeError, ValueError):
                    return None
            if isinstance(identifier, str):
                result[identifier] = {"view_count": count("viewCount"), "like_count": count("likeCount"),
                                       "comment_count": count("commentCount")}
    return result


def upload_body(settings, story, made_for_kids, *, privacy="private", title_hook=None, description_summary=None, extra_tags=None):
    if type(made_for_kids) is not bool:
        raise YouTubeError("Choose the audience setting before uploading")
    if privacy not in {"private", "public"}:
        raise YouTubeError("Choose Private or Public before uploading")
    cast_story = is_recurring_cast_story(story)
    viral_mode = settings.raw.get("dashboard_brief", {}).get("video_mode") == "viral"
    try:
        if cast_story:
            title = youtube_title(story, hook_override=title_hook)
        else:
            # Do not impose the recurring cast on non-cast videos.
            title = (title_hook or story.title).strip()[:100] or story.title
        title = roman_upload_title(title, story.story_category, viral=viral_mode)
        summary = strip_disclosure(description_summary) if description_summary else strip_disclosure(story.description)
        if cast_story:
            tags = youtube_tags(settings, story, extra=extra_tags)
        elif viral_mode:
            tags = viral_tags(settings, story, extra=extra_tags)
        else:
            tags = list(settings.publishing["tags"])
        if cast_story:
            hashtags = combined_hashtags(story.hashtags, present=detected_cast(story.narration))
        elif viral_mode:
            hashtags = viral_hashtags(story)
        else:
            hashtags = story.hashtags
    except ValueError as error:
        raise YouTubeError(str(error)) from None
    description = "\n\n".join(s for s in (summary, story.youtube_disclosure) if s)
    if not story.youtube_disclosure:
        description += ("\n\nThis video is based on publicly available information and may be simplified for "
                         "a short video. AI-generated narration and visuals." if viral_mode else
                         "\n\nThis is an original fictional AI-generated story created for entertainment.")
    return {"snippet": {"title": title,
                         "description": description + "\n\n" + " ".join(hashtags),
                         "tags": tags, "categoryId": settings.publishing["category_id"],
                         "defaultLanguage": settings.brand["language"], "defaultAudioLanguage": settings.brand["language"]},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids,
                       "containsSyntheticMedia": True}}


def upload_video(
    settings: Settings,
    story: StoryPackage,
    video: Path,
    privacy: str = "private",
    publish_at: datetime | None = None,
    *,
    client=None,
    confirmed_body=None,
    confirmed_overrides=None,
    verified_channel_id=None,
) -> dict:
    from googleapiclient.http import MediaFileUpload

    if privacy not in {"private", "public"} or publish_at is not None:
        raise YouTubeError("Only a separately confirmed Private or Public test upload is enabled")
    if not confirmed_body or not verified_channel_id or client is None:
        raise YouTubeError("Use the verified dashboard upload preview; automatic and CLI uploads are disabled")
    if confirmed_body.get("status", {}).get("privacyStatus") != privacy:
        raise YouTubeError("Privacy setting changed after confirmation; review it again")
    audience = confirmed_body.get("status", {}).get("selfDeclaredMadeForKids")
    body = upload_body(settings, story, audience, privacy=privacy, **(confirmed_overrides or {}))
    if body != confirmed_body:
        raise YouTubeError("Upload metadata changed after confirmation; review it again")
    channel = identify_channel(client)
    if not channel["matches_expected"] or channel["id"] != verified_channel_id:
        raise YouTubeError("The upload client is not connected to the confirmed destination channel")
    request = client.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True),
        notifySubscribers=False,
    )
    response = None
    # Retain the existing resumable-chunk uploader, with retries explicitly off.
    # Bound empty/stalled progress so a broken transport cannot loop forever.
    chunks_remaining = (video.stat().st_size // (8 * 1024 * 1024)) + 10
    while response is None and chunks_remaining:
        _, response = request.next_chunk(num_retries=0)
        chunks_remaining -= 1
    if response is None:
        raise YouTubeError("Upload did not finish within the bounded chunk count; outcome is uncertain. Do not retry.")
    identifier = response.get("id", "")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise YouTubeError("Google did not return a valid video ID; upload outcome is uncertain. Do not retry.")
    # Never assume the request's privacyStatus was honored; only display what Google actually returned.
    returned_privacy = response.get("status", {}).get("privacyStatus")
    return {"video_id": identifier, "privacy_status": returned_privacy if isinstance(returned_privacy, str) and returned_privacy else "unknown"}


def set_thumbnail(client, video_id: str, image: Path) -> None:
    """Sets a video's custom thumbnail from a local image. Google requires the destination
    channel to be phone-verified (https://www.youtube.com/verify) for this specific call;
    an unverified channel gets a clear message here instead of a raw provider error. The
    video itself is never affected by a failed or skipped thumbnail call."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise YouTubeError("That is not a valid YouTube video ID")
    if image.is_symlink() or not image.is_file():
        raise YouTubeError("No thumbnail image is available for this video")
    try:
        client.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image))).execute()
    except HttpError as error:
        reason = ""
        try:
            reason = json.loads(error.content.decode())["error"]["errors"][0]["reason"]
        except Exception:  # noqa: BLE001, S110 - best-effort parse only; fall through to the generic message
            pass
        status = getattr(getattr(error, "resp", None), "status", None)
        if reason == "youtubeSignupRequired" or status == 403:
            raise YouTubeError("Custom thumbnails need a phone-verified YouTube channel. Verify at "
                                "youtube.com/verify, then try again. The video itself is unaffected.") from None
        raise YouTubeError("Setting the custom thumbnail failed. The video itself is unaffected; do not retry blindly.") from None
