# Local Shorts dashboard

## Start and stop

Double-click `start-dashboard.command` in Finder. It uses the existing `.venv`,
opens the browser automatically, and binds only to `127.0.0.1:8765`.
There is no daemon, scheduler, deployment, OAuth or YouTube integration in this UI.
Leave the launcher window open. Press Ctrl+C there to stop; any already accepted
request is allowed to save its result, then no subsequent request starts.

Equivalent single command, if needed:

```sh
./start-dashboard.command
```

If dependencies need installing into the existing virtual environment:

```sh
.venv/bin/pip install -e '.[dev]'
```

## Buttons

- **Generate Story** first opens a confirmation page showing model, estimated cost,
  conservative reserve and remaining allowance. Confirmation makes exactly one
  request. Nothing automatically requests a second story.
- **Create Video From This Story** saves final approval for the exact current revision,
  immediately prepares eight consistent visual prompts and
  runs the existing `Pipeline` for images, speech, alignment, captions and rendering.
  This button authorizes those paid stages within the remaining allowance. There is
  no independent paid review and no additional editorial approval.
  **Your approval is final. Production will begin immediately.**
- **Generate Another Story** preserves the old revision and charges the same project
  for one new draft. The prompt includes earlier ideas and requires different objects,
  settings, characters and ending mechanisms; local similarity warnings are advisory.
- **Edit Story** is free. It saves a new revision, redistributes the unchanged words
  across eight narration scenes, reruns local checks and clears the draft scores.
  It discards every old scene direction, character/location description and originality
  descriptor. Visual planning happens immediately after your final approval, using
  the edited narration. A saved source hash prevents resuming a plan for different text.
- **Reject Story** preserves the story and does not start other work.
- **Cancel** allows an accepted request to finish saving, then stops at the next safe
  boundary. A local FFmpeg process can finish its current operation before cancellation.
- **Resume saved work** is explicit permission to continue unattempted paid stages
  within the same allowance. Completed requests/assets are never regenerated.
- **Download** and **Open local output folder** use only known project assets. Folder
  opening is macOS-only and uses a fixed argument array, not a shell command.

## Defaults and quality

Mystery with Final Twist; 50–60 seconds; very simple conversational English; generation
target 115–130 words, allowed 115–135; eight portrait scenes; $0.50 per-project allowance;
manual story approval. Estimated duration is at most 60 seconds, calculated at 135 words
per minute, not a guarantee of actual speech timing. New duration choices never exceed 60.
All stories need a strong opening, coherent connected events, natural sentence variation,
an understandable ending and original, non-documentary writing. Local readability gates require
Flesch ease >=85, grade <=4, average sentence length <=10 words and maximum sentence
length <=14 words. Every category follows the permanent **Very Easy English** contract:
first-listen comprehension for ages 8–10 without childish or repetitive storytelling.
Use everyday words, one main idea per sentence, active voice, short natural dialogue,
clear actions in event order and a clear ending. Keep science-fiction ideas simple.

The free local checker runs after generation and every manual edit, and again before
approval. It combines offline [wordfreq frequency data](https://github.com/rspeer/wordfreq),
heuristic syllables, sentence structure and nearby plain-language definitions. There
is no banned-vocabulary list or replacement table. Messi, Ronaldo and their first
names are exempt from familiarity checks and treated as familiar one-syllable words
in the displayed readability estimates; spoken word counts still include their names.
Highlighted words/sentences show exact issues and explanations on the story page.
A passing draft displays **Easy enough for children**. These are screening estimates,
not validated child-comprehension scores; adult corpus frequency can misjudge familiar
childhood words. Metaphors, chronology, entertainment and context still need human judgment.
The generation prompt spells out those requirements in the existing structured API
format; it does not add a reviewer or a second request.

Failed drafts are saved and displayed with all language problems. They never trigger
an automatic retry, replacement or paid review. Edit freely or explicitly confirm
Generate Another Story. Blocking free local errors must be resolved before production.
Reports are saved in each revision's `draft-evaluation.json`; refreshing or restarting
rechecks locally without another API request. Completed files remain untouched.

Generation follows the selected rubric in `src/elsewhere/rubrics.py`.
Suspense, danger, horror and final twists are not global requirements. Funny is judged
on comedy and punchline, Emotional on emotional impact and relationship, Inspirational
on meaningful effort and a hopeful payoff, and the other categories on their own criteria.
Only Mystery with Final Twist mandates the final 5–8-second twist and delayed explanation.
Random chooses one category internally before generation and shows it on confirmation,
story and history pages. The saved choice survives editing, regeneration and restart;
it never combines arbitrary genres or costs an extra API request.

The five global scores are **simple language, opening hook, coherence, category match,
and ending payoff**, plus the displayed category-specific scores. Saved author scores
are advisory only: no numeric score threshold blocks your final approval. Editing may
leave scores unavailable; no review is requested to fill them in.
Approval reruns only free local validation: 115–135 words, estimated duration <=60s,
simple-language readability, required title/story, selected category metadata,
clearly prohibited references and valid scene structure. Local category metadata
matching cannot independently judge literary category fit; that decision is yours.
Old reviews remain in history but never control production. The dashboard's request
guard and API service both prohibit paid story reviews. Human approval is tied to a
hash of the exact story, preventing substitution on resume.
Saved project metadata records the requested and selected categories and rubric version.
Legacy drafts, reviews and itemized request records are preserved unchanged.

These heuristics and model judgments cannot guarantee originality, copyright safety,
natural narration, exact visual continuity, or YouTube monetization. A completed video
still needs human viewing/listening. Existing narration duration/quality checks remain
active: if a paid performance cannot be brought into range naturally, production stops
with the saved audio rather than silently buying another performance.

## Storage, resume, and costs

### Recurring cast and expressive narration (new stories)

New projects use exactly Lionel Messi and Cristiano Ronaldo as recognizable synthetic
public figures in polished, cinematic, slightly stylized illustrations, never fake
photographs or claims about real events. Their fictional roles vary by category;
football is not the default plot. Only they may be named or speak. Background people
must remain anonymous, silent and incidental. The fixed face/body descriptions and
the story-specific continuity bible (clothing, room and props) repeat in every image prompt.
No club/national kits, trademarks, logos, sponsors, trophies, endorsements, scandals,
sexual/hateful content, graphic violence, serious crimes, drugs or humiliation.
These protections take priority over genre conventions. Crime requires intentional but
harmless fictional wrongdoing (a playful theft, trick or secret plan), connected clues
and a clear solution. A missing object or accidental mistake alone does not qualify.
Free local textual checks flag accidental endings and absent intent, and cap displayed
category-match/investigation/consequence scores at 3/10 when contradicted by the text.
The original model self-scores stay saved; the dashboard explains its local correction.
These are conservative pattern checks, not a guarantee of semantic category fit.
Thriller uses safe urgency, and Horror uses harmless fantastical unease.

The backend saves this exact disclosure in `youtube_disclosure`, separately from narration:

> This is an unofficial, fictional AI-generated story created for entertainment. It is not affiliated with or endorsed by Lionel Messi, Cristiano Ronaldo, their clubs, sponsors, or representatives.

Saved story packages have separate `title`, `narration`, `scenes` and `youtube_disclosure`
fields. The complete `narration` field is the only spoken/measured text; scenes must
partition it exactly in order. Legacy packages without that field remain readable locally.
The model's separate [Structured Outputs schema](https://developers.openai.com/api/docs/guides/structured-outputs)
requires narration but has no disclosure field. Generation instructions explicitly
forbid producing disclosure text; the fixed wording is not sent to the writer.
The backend adds it after parsing and separates any leaked fixed boilerplate without
altering the raw response archive. Caption, performance, TTS and image entry points
also reject leaked disclosure text before paid work. Future YouTube description assembly
joins descriptive metadata with `youtube_disclosure`; it never feeds media generation.
Manual editing strips the fixed boilerplate before scene splitting. Narration is not
rebuilt from description metadata or extra scenes. A conflicting scene partition blocks
production locally without a retry or paid review.

Project metadata and `production-metadata.json` record all four requested flags as true:
`contains_ai_generated_content`, `contains_synthetic_public_figures`,
`youtube_altered_content_disclosure_required`, `unofficial_fictional_parody`.
These are conservative disclosure metadata, not a claim of legal clearance or endorsement.
Nothing uploads or changes YouTube settings.
New cast stories and visual planning reserve $0.05 per request to allow for longer
cast and continuity instructions; actual calculated usage still determines the charge estimate.

`narration-performance-plan.json` is built free from the exact approved sentences and
saved with each new/edited draft. It contains sentence text, attributed speaker, emotion,
intensity, speed and pause after the sentence. Speaker attribution uses local heuristics;
ambiguous lines remain with the narrator. All lines use only the selected built-in
storyteller voice. No cloning, real voice imitation, or imitation of either man's accent.
`gpt-4o-mini-tts` receives one request with bounded instructions highlighting urgent
shouts, audible whispers, soft/emotional moments, energetic comedy, faster action and
the final sentence. The instruction string is saved in `narration-instructions.json`.
The full plan is retained even when only important sentences are highlighted in the request.
The implementation uses the documented [speech instruction controls](https://developers.openai.com/api/docs/guides/text-to-speech).
Speech input-cost estimates include the performance instructions, not just narration.

There is no automatic split into multiple speech requests or automatic performance redo.
If emotion is inadequate, listen first and explicitly authorize any later change.
Audio normalization, unclipped-volume validation and duration checks remain active.
Prompts, schemas and local reference checks cannot guarantee likeness consistency,
semantic safety, perfect speaker attribution, or convincing emotion; human viewing/listening
is still required. The final-human-approval workflow and no-paid-review guard remain intact.

Existing drafts keep their original cast until an explicit Generate Another Story action.
Completed projects are never migrated, regenerated or rewritten, including during history viewing.
Old review files and costs remain intact. New cast identities do not trigger similarity
warnings simply because the same two men recur.

New dashboard projects live under `output/dashboard-<id>/`. Each story revision has
its own immutable input, review, request ledger and media directory. Previous drafts
and successful request archives are never deleted. `dashboard-project.json` saves
the operation, version, cancellation flag and stage times; polling reads that state.
`cost-report.json` at the project root aggregates **all revisions**, including rejected
drafts and reviews. Per-revision ledgers retain pricing sources, usage and request IDs.

There is no future independent-review cost. Visual planning has a conservative $0.03
reserve; story generation reserves $0.03, each image $0.045, speech
$0.05, alignment $0.01. Costs shown before submission are estimates, not invoices.
Every request checks the remaining project-wide allowance. Output tokens for story
and visual planning are bounded at 6000. SDK retries are zero.
The local gate cannot enforce a provider-side cap on an already accepted request or
limit unrelated account activity. Speech usage can be missing; those charges remain
explicitly estimated/unknown. An ambiguous or interrupted in-progress request blocks
additional paid requests, including regeneration. Check billing before resolving it;
the UI deliberately has no bypass or blind retry button.

Failed projects now show a free downloadable API diagnostic report. New failures retain
HTTP status, recognized provider error code, affected parameter, request ID and a safe
application-written explanation. Raw error messages, request bodies, headers, prompts
and credentials are never included. Unknown provider fields are omitted rather than
echoed. Historical failures without these details remain explicitly unknown: downloading
the report cannot recover a discarded response or clear the uncertain-charge lock.
See the [official error guide](https://developers.openai.com/api/docs/guides/error-codes).
Local image preflight validates model family, quality, supported studio sizes, output
format and prompt length before a paid request is recorded. It cannot verify account
access or predict a moderation decision. No failed request is automatically retried,
and an HTTP rejection alone is not treated as proof of a zero charge.

Restart never automatically runs paid work. It marks active work interrupted and
retains the ledger. A project must be explicitly resumed. If a successful request's
asset is missing, it is not repeated. Structured story responses can be recovered from
the saved response archive without another request. Completed local render failures
can be resumed with the existing assets.

Projects stopped by an old review are shown as ready to create. Edit first if desired,
or click **Create Video From This Story** to resume directly into media planning and
generation. The old review, its known charges and the exact draft are retained. A prior
consent record is archived when you replace it with final approval. Nothing starts
automatically during this workflow migration. Budget and uncertain-charge safeguards
remain active, as do file integrity and technical production checks.

Earlier command-line runs are visible and playable in History **read-only**. They are
not silently migrated, regenerated, or charged against a new dashboard project.
The browser Resume control applies to dashboard-created projects; legacy CLI recovery
remains governed by each old run's saved workflow/configuration.

## Security and implementation

Flask server-rendered HTML, local CSS and a small polling script; no frontend framework
or third-party web assets. Production delegates to the existing `Pipeline`. Backend
callbacks provide cost/cancellation guards and saved progress events.

Host validation plus loopback binding, same-origin POST checks, session CSRF tokens,
durable form-version/idempotency guards, HTML autoescaping, a restrictive Content
Security Policy, a size-limited form body and strict backend option validation protect
the local interface. Only allowlisted assets can be served; symlinks/path traversal and
raw API response archives are excluded. Video Range requests support playback/seeking.
The `same-origin` referrer policy preserves native local form origins while omitting
referrers to external sites. Null or foreign origins remain blocked. If an older tab
reports a hidden-origin error, reopen New Video or refresh the saved project page.
API keys come from the existing backend `.env`; they never appear in browser state,
templates, JavaScript, logs or JSON responses. Only whitelisted numeric usage counters
are exposed. Debug mode and raw request/exception logging are not enabled.

One OS-level file lock prevents two dashboard servers from operating on the same
workspace. Workers exist only for explicit user actions and finish on shutdown. There
is no permanent background service or automatic scheduling.

## Offline verification

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
node --check src/elsewhere/static/dashboard.js
```

The entire test suite blocks socket connections and DNS through `tests/conftest.py`.
Dashboard tests inject mocked API responses and media
operations into the actual pipeline. They cover final manual approval, historical rejection bypass,
regeneration/history, editing, duplicate submissions, cumulative spending, ambiguous
failures, cancellation, resume, restart recovery, output preservation, CSRF/Host checks,
secret redaction, closed structured-output schemas and video byte-range responses.
Edited-story tests verify that old directions cannot reach review/planning/images;
refresh and reconstructed-server tests verify unchanged costs and reuse of saved assets.
Mock files live only in temporary test directories. No paid API request or real story,
speech, image or video generation is needed to build or test this dashboard.

Official implementation references:

- [Responses API request controls](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs): new score responses use closed required-field schemas; legacy schemas are local-read compatibility only.
- [Flask documentation](https://flask.palletsprojects.com/en/stable/quickstart/)

## Files changed for this dashboard

- `pyproject.toml`: Flask and offline wordfreq dependencies, optional console entry point and template/static packaging.
- `README.md`, `DASHBOARD.md`: browser usage and safety/testing documentation.
- `start-dashboard.command`: Finder-friendly foreground launcher.
- `src/elsewhere/dashboard.py`: local server, routes, security, media serving and startup/shutdown.
- `src/elsewhere/dashboard_store.py`: durable manual workflow, history, cost aggregation and cancellation.
- `src/elsewhere/models.py`: closed story-response metadata and visual-planning schema.
- `src/elsewhere/rubrics.py`: category-specific rules, global/category score labels and duration estimates.
- `src/elsewhere/safety.py`, `config.yaml`: category-aware approval gates and 60-second Short limit.
- `src/elsewhere/prompts.py`: permanent story rules, diverse drafts and review scope.
- `src/elsewhere/easy_english.py`, `readability.py`: corpus familiarity, context/syntax screening, cast-exempt readability and exact highlight spans.
- `tests/test_easy_english.py`: all-category English contract, stricter numeric limits, name exemptions, context and malformed-text checks.
- `src/elsewhere/openai_service.py`: bounded story output, prohibited dashboard review requests and post-approval visual planning.
- `src/elsewhere/costs.py`: pre-request guard, change notifications, immediate response archives and uncertainty labels.
- `src/elsewhere/pipeline.py`: reusable service/ledger/progress hooks and immutable final-human-approval media path.
- `src/elsewhere/renderer.py`: caption/render progress, cancellation boundaries, comma-safe validation retained, and additional technical metadata.
- `src/elsewhere/templates/base.html`, `new.html`, `confirm.html`, `project.html`, `panel.html`, `history.html`, `saved_story.html`, `legacy.html`, `error.html`.
- `src/elsewhere/static/dashboard.css`, `dashboard.js`.
- `tests/test_dashboard.py`: mocked workflow, route and security tests.
- `tests/conftest.py`: automatic network blocking for the complete offline suite.
- `tests/test_rubrics.py`, `tests/test_fixed_story.py`: category contract coverage and updated mocked review responses.
- `src/elsewhere/recurring_cast.py`, `src/elsewhere/performance.py`: cast contract, disclosures and free performance planning.
- `src/elsewhere/disclosure.py`, `tests/test_disclosure.py`: metadata separation and offline narration/measurement/caption/TTS/image/Crime regression checks.
- `tests/test_recurring_cast.py`: offline cast/schema/disclosure/performance and single-request TTS tests.
- `scripts/create_brand_assets.py`: import-order-only Ruff correction; asset generation was not run.

`.env` and existing output files are not modified by the dashboard build. Installing
Flask updates only the local `.venv`. Starting the app creates a transient
`data/dashboard.lock` file; the OS releases the lock when the foreground process exits.

## Explicit one-attempt image diagnostic

`src/elsewhere/image_diagnostic.py` supports a separately authorized Image 1
diagnostic through the existing image service. It is not a dashboard action or
an automatic retry. The caller must hold the dashboard process lock. It reserves
$0.20 for the prior unknown charge and $0.20 for the single new attempt within
the project's allowance (never above $0.50). These are local estimates, not an
OpenAI billing cap. The saved prompt, model and original cost ledger stay intact.

The exclusive `revision-0001/image-diagnostic-0001/` directory records consent,
safe diagnostics and a separate cost ledger, which is included in dashboard
totals. Its existence prevents repeating the attempt after interruption. Never
delete that directory to retry. Uncertain charges still block normal production;
a safety rejection must not be bypassed. No later media requests run as part of
the diagnostic.
