# Local YouTube connection and Private test uploads

Start the dashboard with `./start-dashboard.command`, then open
<http://127.0.0.1:8765/youtube>. Startup, page views and video generation never
start OAuth or upload. The app remains bound only to `127.0.0.1`.

The Desktop client is stored at `secrets/youtube-oauth-client.json`. Tokens are
stored at `secrets/youtube_token.json`. The directory has mode 0700 and credential
files mode 0600. The entire directory is ignored by Git. Never paste these files
into chat or serve them through the dashboard. Only an `installed` Desktop client
using Google's authorization/token endpoints is accepted.

## Connect

Click **Connect YouTube**. Select **One Minute Elsewhere (@OneMinuteElsewhere1)**
if Google asks which channel to use, not a personal channel. The installed-app
flow uses a temporary 127.0.0.1 loopback listener, a random port, OAuth state and
PKCE. It waits at most three minutes. Callback URLs, credentials and tokens are
not printed. No upload occurs during connection or a connection test.

Exactly two scopes are requested:

- `https://www.googleapis.com/auth/youtube.upload`
- `https://www.googleapis.com/auth/youtube.readonly`

Read-only access is used solely for `channels.list`: identify the authenticated
channel with `mine=true`, resolve the expected handle, and compare channel IDs.
The broader `youtube` and `youtube.force-ssl` scopes are never requested. Missing
or extra granted scopes are rejected instead of silently expanding permissions.

**Test connection** refreshes verification. **Disconnect** deletes the local token
and clears local verification; it does not delete videos or revoke the grant in
Google account settings. Connection status is cached for page views and rechecked
before upload. Failed re-verification locks uploads again.

## Upload only after a separate preview

The connection page lists completed, locally validated outputs, including earlier
CLI videos. After channel verification, choose a video and explicitly select its
audience. New stories are child-directed, but the audience setting must be chosen
for the particular completed video; it is not silently inferred.

The preview shows the exact video, file path/hash, title, description, tags, audience,
AI disclosure, requested privacy and destination channel. Check the confirmation and
click **Upload Private/Public Test**. The existing `youtube.upload_video` chunked
uploader is reused; no separate upload implementation or background upload job exists.

Every request sets `containsSyntheticMedia=true`, the confirmed `selfDeclaredMadeForKids`
value, and the explicitly chosen `privacyStatus` (`private` or `public`; never inferred).
Subscriber notifications are disabled. Unlisted, scheduled, bulk and automatic uploads
remain unavailable.

This project has not passed YouTube's required API audit. Google restricts uploads
from unaudited API projects to Private and may force a requested Public upload back to
Private, regardless of what was requested — this dashboard does not attempt to work
around that restriction. It never assumes the request was honored: `videos.insert`'s
own response is what gets recorded as the "returned" status, and a **Check current
status on YouTube** button (read-only, `youtube.readonly` scope) refreshes the live
value at any time, since a video's status can also change later by hand in YouTube
Studio, outside this API project entirely.

An exclusive upload record is saved **before** sending the video. The returned
video ID is saved immediately. Duplicate form submissions and duplicate uploads
of identical video bytes are rejected, including after restart. An interruption
or unclear failure stays in-progress/unknown and is never automatically retried.
Check YouTube Studio before investigating an uncertain result. Do not delete an
attempt record to force another upload. This conservative behavior may block a
retry even if Google ultimately received no video.

Connection metadata, one-use form markers, exact previews and upload records live
under ignored `data/youtube/`. Existing completed outputs, narration and API cost
reports are not modified. Only sanitized channel metadata is displayed; Google
error payloads and resumable-session URLs are never exposed.

## Offline testing

Run `.venv/bin/ruff check .` and `.venv/bin/pytest -q`.
`tests/conftest.py` blocks networking for the entire test suite. YouTube tests
mock OAuth, channel lookup and upload responses; no authorization or upload is
performed by the tests.

Sources checked during implementation:

- [Google installed Desktop OAuth flow](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Channel lookup and authenticated-channel filter](https://developers.google.com/youtube/v3/docs/channels/list)
- [Private upload restriction and videos.insert fields](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Synthetic-content and audience status fields](https://developers.google.com/youtube/v3/docs/videos)
