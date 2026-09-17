# Viral mode — local Shorts dashboard

Start with `./start-dashboard.command`, then open http://127.0.0.1:8765/.

Choose **Viral** or **Messi & Ronaldo** at the top of New Video. Viral is a
separate inspiration mode, not a story category; Funny, Mystery, Inspirational
and the other category rubrics still apply. The recurring cast stays unchanged
in Messi & Ronaldo mode. Viral uses invented characters and original harmless,
child-friendly fiction—not fabricated news about real events.

## Finding a topic

Select **Viral**: a free lookup immediately selects and displays the top available
candidate and its suggested language. It does not generate a story. No country
selection is needed. **See Top Topic and Alternatives — free** shows the ranked
alternatives with the first topic already selected. Without JavaScript, this
button provides the same free workflow. Generate Story still leads to paid confirmation.

- The source is Google Trends' public RSS feed for **Google Search interest**.
  Entries must be timestamped within the past 24 hours. These are not a ranking
  of the most-searched YouTube topics. Unavailable/empty feeds are reported
  honestly; no fallback topics or rankings are invented.
- Optional recent YouTube matches use the YouTube Data API's `search.list`,
  restricted to videos published in the last 24 hours. Matches are evidence of
  recent videos, not proof of popularity or guaranteed virality.
- Discovery now attempts **58 regions** across the Americas, Europe, Africa,
  Asia, the Middle East and Oceania. Exact region codes are in `WORLD_REGIONS`
  in `src/elsewhere/trends.py` and on the topic alternatives page. Coverage is
  not every country or language. Failed/empty sources reduce coverage.
- Up to 100 entries per feed are checked, then deduplicated by normalized title.
  Regional volume lower bounds are divided by trend age (minimum one hour) and
  summed to produce an **estimated momentum score**. This is a volume/recency
  proxy, not measured acceleration, Google baseline growth, or YouTube searches.
  Language variants may not deduplicate. Missing traffic stays unknown, never
  zero; if every topic lacks traffic, a clearly labeled recent-topic fallback
  is selected. Never claim a verified worldwide #1 or guaranteed virality.
- Eight request-scoped workers run at most 58 feed lookups; collection has a
  30-second deadline and cancels unstarted work. In-flight requests must finish
  their timeout before the response completes. No permanent research workers
  or scheduler exist. Optional YouTube evidence remains at most three searches,
  without a region filter. Results are cached for 30 minutes across clicks.
- No subscription, paid AI research, or OAuth connection is needed for the
  Google Trends feed. If desired, put a separate `YOUTUBE_DATA_API_KEY` in the
  ignored backend `.env` file and enable YouTube Data API v3 in its Google
  project. This uses Google's API quota. It is never sent to the browser or
  placed in a URL. The existing OAuth channel credentials/scopes are not used.
- Feed text is untrusted data, not instructions. Basic topic screening is
  incomplete, particularly outside English. Inspect the topic and story.

## Language and approval

Auto uses only the topic's writing and cultural clues, never its feed country.
For example: Bollywood/Indian-context topics suggest Hindi, Tamil/Chennai topics
suggest Tamil, and Urdu/Pakistani-context topics suggest Urdu. General topics or
conflicting regional clues fall back to English. These are free, fallible local
heuristics, not a semantic model or universal language detector. Shared scripts
can be ambiguous. **You can override before confirming the story request.**
Old cached country-based suggestions are recalculated for newly selected topics;
saved projects keep their selected language so existing media/resume is unchanged.

Hindi, Tamil and Urdu use native-script narration and captions. Image directions
stay in English. One selected AI narrator performs the script; no voice cloning.
Word alignment uses transcription in the selected language, not translation.
Unicode matching retains native letters and vowel marks. Captions use local
script-capable macOS fonts and the existing two-line safe-area layout.

English Flesch/grade formulas are not applied to other languages. Native-language
word counts and timing are provisional; vocabulary, category match,
pronunciation, naturalness, script shaping and actual timing require listening
and visual inspection. English-only local semantic checks are not represented
as multilingual safety verification. No paid reviewer is added.

After selecting a topic, the confirmation page shows the story model, estimated
story cost, conservative request reserve, language and remaining allowance.
You must confirm this paid request. Read/edit the resulting story, then click
**Create Video From This Story** to approve media production. No automatic
replacement story, OAuth or upload happens in this workflow.

## Cost and saved state

Initial trend lookup adds **$0.00** to the project's itemized report, with its
free/quota-based basis. There are **no paid AI research requests** in this version.
Normal story, visual planning, eight images, speech and alignment costs still
apply and depend on the configured models and returned usage. Native-language
token counts can differ. The existing per-request conservative budget checks
remain; the default video allowance is $0.50, not a guaranteed finished price
or provider billing cap. Paid failures retain existing pipeline handling.

Topic, country, suggested/selected language, source URLs and timestamps are
saved in the project; every revision includes `trend-source.json`. The original
snapshot is retained under ignored `data/trends/`. Resume does not fetch a new
topic or change language. Existing projects default to their English workflow,
and completed files are never rewritten by this feature. Future upload metadata
uses the video's saved language rather than the channel's default language.

## YouTube titles

Upload previews and the reused uploader now always produce Roman-script titles.
Native writing is romanized locally using Unidecode, with no API translation or
additional paid request. This is approximate transliteration, not necessarily
idiomatic Roman Hindi/Urdu; review and edit the hook before uploading. Optional
mixed-script titles are not enabled in this version: the title is entirely Roman.

A category label such as **Funny Story**, **Mystery Story** or **Emotional Story**
is always included; Viral-mode projects use **Viral Funny Story**, etc. This is
the selected content format's label, not evidence the video has already gone viral.
Titles end in #Shorts, stay within 100 characters and preserve the required cast
prefix when applicable. The exact transformed title appears in the upload preview.
Changes affect future previews/uploads only; existing videos, story narration,
captions, performance plans and upload history are not rewritten. Changed metadata
invalidates an old unsubmitted upload preview until you review the new title.

## Offline verification

`./.venv/bin/ruff check .`

`./.venv/bin/pytest -q`

`tests/conftest.py` blocks network access. Viral tests inject mock feeds and
provider responses and cover cache limits, source provenance, stale-topic
rejection, language overrides, prompt contracts, duplicate submissions,
restart recovery, native captions, single-request TTS parameters and alignment.
No live topic search, story, voice or video is needed to test the integration.

Implementation verification: Ruff passed; 23 Viral tests passed. The complete
offline suite returned 373 passed and two pre-existing failures:
`test_failure_persists_safe_reason_but_keeps_unknown_charge_and_blocks_retry`
and `test_readability_gate_rejects_long_sentences_before_assets`. They concern
existing rejected-request charge handling and an old blocking-readability
expectation, respectively; this feature does not change those behaviors.
Hindi, Tamil and Urdu single-frame caption tests also rendered locally and
passed pixel-bound safe-area checks. Live feeds and real speech were not tested.

## Files changed for this feature

- `.env.example` (empty placeholders only), `.gitignore`, `VIRAL.md`
- `src/elsewhere/languages.py`, `src/elsewhere/trends.py`
- `src/elsewhere/dashboard.py`, `src/elsewhere/dashboard_store.py`
- `src/elsewhere/templates/new.html`, `src/elsewhere/templates/trends.html`
- `src/elsewhere/templates/confirm.html`, `src/elsewhere/templates/panel.html`
- `src/elsewhere/prompts.py`, `src/elsewhere/safety.py`
- `src/elsewhere/captions.py`, `src/elsewhere/renderer.py`
- `src/elsewhere/openai_service.py`, `src/elsewhere/performance.py`
- `src/elsewhere/scene_plan.py`, `src/elsewhere/youtube_dashboard.py`
- `tests/test_viral.py`

No existing output files were changed by the restart (1,037-file inventory
unchanged). The actual `.env` and OAuth credentials were not modified.

## Official references

- [Google Trends trending searches](https://support.google.com/trends/answer/3076011)
- [YouTube search.list parameters](https://developers.google.com/youtube/v3/docs/search/list)
- [YouTube API quota and setup](https://developers.google.com/youtube/v3/getting-started)
- [OpenAI speech generation and languages](https://developers.openai.com/api/docs/guides/text-to-speech)
- [OpenAI transcription and word timestamps](https://developers.openai.com/api/docs/guides/speech-to-text)
