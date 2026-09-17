# One Minute Elsewhere

## Local browser dashboard

Double-click **start-dashboard.command** in Finder. It opens
**http://127.0.0.1:8765** and stays local. Close the dashboard with Ctrl+C in its
launcher window. No API call is made on startup, page load, or refresh.

Use **Generate Story** to see and confirm the paid request estimate. Read or edit
the story, then choose **Create Video From This Story** to start production within
the project allowance. **Your approval is final. Production will begin immediately.**
No paid story review is requested; historical reviewer rejections cannot stop production.
All drafts and costs remain in history. **Cancel** stops before the next request;
**Resume saved work** reuses completed assets and never retries ambiguous requests.
Generation follows your selected category, not a universal suspense formula.
Random saves one category and shows the choice before generation. Editorial scores
are advisory only. Free local content, language, length and structure checks remain.
New stories feature Messi and Ronaldo in harmless unofficial illustrated fiction,
with the required disclosure and one expressive AI narrator—not their real voices.
Completed projects remain unchanged. See the cast and narration details in DASHBOARD.md.

Every category now uses **Very Easy English** for ages 8–10 and older audiences:
115–130 words (hard maximum 135), Reading Ease ≥85, grade ≤4, average sentence
≤10 words and maximum sentence ≤14 words. A free local checker highlights difficult
parts after generation and editing. Failed drafts stay saved; no replacement is bought
automatically. Messi and Ronaldo's names are exempt from language difficulty checks.

See [DASHBOARD.md](DASHBOARD.md) for controls, safety boundaries, testing, and files.
The command-line instructions below remain available for the earlier prototype.

An original-fiction production pipeline for YouTube Shorts. The local dashboard generates
one story for your final approval, performs free local checks, and creates scene art,
narration and burned-in captions. No dashboard review or upload request is made.

The launch configuration produces two English mystery or science-fiction Shorts per day and one
long-form story per week. Public publishing is intentionally disabled by default.

## Safety baseline

- Original fictional concepts only
- No downloaded clips, copyrighted characters, celebrities, real crimes, or current tragedies
- Local blocked-reference and near-duplicate checks
- Final human approval in the dashboard; editorial scores are advisory only
- Synthetic-media disclosure in YouTube metadata
- First uploads remain private until manually inspected

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
elsewhere doctor
```

Add the API key to `.env` on your own computer or server. Never send it through chat.

Run a no-cost rendering test:

```bash
elsewhere run --format short --dry-run
```

Show the models, cost assumptions, and local spending guard without calling the API:

```bash
elsewhere cost-plan
```

After explicit spending approval, create exactly one production test video without uploading:

```bash
elsewhere run --format short --fixture eighth-shadow-final --max-cost-usd 2.00 --approve-paid
```

If a local render or validation step fails, retain the job directory and resume it without
regenerating successful paid assets:

```bash
elsewhere run --format short --resume output/YYYYMMDDTHHMMSSZ-one-video-test --max-cost-usd 2.00 --approve-paid
```

If a paid transcription succeeds but returns malformed word timing, recover alignment locally
without repeating the paid request:

```bash
pip install -e '.[recovery]'
python scripts/recover_alignment.py output/YYYYMMDDTHHMMSSZ-one-video-test
```

Each job contains `story.json`, eight scene images, narration, measured word alignment,
`captions.srt`, `cost-report.json`, validation details, and `final.mp4`. API calls have SDK retries
disabled. A request that fails with an uncertain charge is not retried automatically.

## One-time YouTube authorization

1. Create a Google Cloud project.
2. Enable YouTube Data API v3.
3. Configure an OAuth consent screen for an external desktop application.
4. Download the OAuth client JSON as `secrets/youtube_client_secret.json`.
5. Run `elsewhere auth-youtube` and approve access to the correct channel.

Test with a private upload:

```bash
elsewhere run --format short --upload --privacy private
```

Start continuous automation after OAuth is connected:

```bash
docker compose up -d scheduler
```

The scheduler stays in private-test mode while `AUTOPUBLISH=false`. After five inspected uploads,
set `AUTOPUBLISH=true` and restart it. From that point, the configured cadence requires no daily
input.

Do not switch to public automation until at least five private outputs pass human review. YouTube
may restrict uploads from a new, unverified API project to private until the project passes its
required compliance review.

## Operating cost controls

The default image model is GPT Image 2.5 Flare at medium quality, with eight images per Short.
FFmpeg supplies camera movement and video rendering locally, avoiding per-second AI video costs.
Change image quality to `low` in `config.yaml` for inexpensive testing.

## Publishing cadence

Initial local-time targets in `config.yaml` are 04:00 and 21:00 Asia/Karachi for Shorts, reaching
different portions of the US day. The weekly long-form target is Saturday at 22:00. These are
starting hypotheses and should be changed after four weeks of YouTube audience data.
