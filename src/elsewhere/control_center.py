"""One-click production glue for the Control Center.

This module creates NO new story, image, narration, alignment, caption, rendering or
upload logic. It only: (1) automatically chooses which existing dashboard options to use,
then calls the same `DashboardStore.create`/`dispatch` the manual "New Video" form calls,
and (2) once that pipeline finishes, calls the same `YouTubeDashboard.preview`/`upload` the
manual YouTube tab calls. A background thread just waits for each stage to go idle and
moves to the next one, exactly like a human clicking "Create Video" and then "Upload"
without stopping to look. Manual production and manual YouTube upload are completely
unaffected and remain available.
"""
from __future__ import annotations

import random
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from . import youtube
from .costs import utc_now
from .dashboard_store import STYLES, VOICES, DashboardStore, NewVideo, read_json
from .models import StoryPackage
from .openai_service import save_json
from .safety import similarity
from .youtube_dashboard import YouTubeDashboard, best_known_privacy

MONTHLY_VIDEO_LIMIT = 200
MONTHLY_SPEND_LIMIT_USD = 40.0
MONTHLY_VIDEO_WARNING = 180
MONTHLY_SPEND_WARNING_USD = 35.0
PER_VIDEO_SPEND_LIMIT_USD = 0.50  # The existing NewVideo default; never overridden higher here.
STATS_CACHE_SECONDS = 15 * 60
TERMINAL_PHASES = {"complete", "upload_failed", "failed", "cancelled", "interrupted"}
# These specific warnings are advisory in the manual flow (a human can approve anyway),
# but the one-click flow has no human to consult, so it treats them as stop conditions.
STOP_WARNING_MARKERS = ("story category does not match the selected category", "Too similar to an earlier story")

# Deliberately wide and mixed: physical objects, competitions, challenges, projects and events,
# not just "a mysterious object turns up" every time. Matches recurring_cast.LIFE_VARIETY's own
# instruction to explore work, study, home, travel, friendship, art, food, farming, repairs,
# community life and harmless fantasy, and to avoid repeating the same trope story after story.
LOCATIONS = ["a quiet seaside town", "a busy night market", "a small mountain village", "an old countryside farm",
             "a rooftop garden in the city", "a snowy ski lodge", "a sunny neighborhood bakery",
             "a riverside repair workshop", "a small desert research camp", "a rainy train station",
             "a lively summer street fair", "a small-town public library", "a community swimming pool",
             "a busy weekend farmers market", "a quiet forest campsite", "a bright neighborhood art studio",
             "a small-town radio station", "a cozy winter cabin", "a colorful spring flower show",
             "a bustling school science fair", "a beachside volleyball court", "a small family diner",
             "a community theater backstage", "a neighborhood bike repair shop", "a rural county fair"]
OBJECTS = ["a cracked pocket watch", "a mysterious wooden box", "a tangled fishing net",
           "an old family recipe card", "a broken kite", "a jar of mismatched buttons",
           "a faded hand-drawn map", "a squeaky garden gate", "a half-finished painting", "a lost set of keys",
           "a neighborhood baking contest with one hour left", "a leaky roof before a big storm",
           "a jammed water pump the whole street depends on", "a talent show act that keeps going wrong",
           "a garden competition with the judges arriving soon", "a homemade go-kart that will not start",
           "a school science project due the next morning", "a parade float missing its final piece",
           "a charity fun run short on volunteers", "a birthday cake that collapsed an hour before the party",
           "a stray puppy that needs a home before nightfall", "a power outage during a big neighborhood dinner",
           "a leaking rowboat during a river race", "a broken telescope on the one clear night of the year",
           "a music recital missing its final song", "a community mural short on paint",
           "a swimming relay team down one swimmer", "a picnic threatened by a sudden downpour"]
ENDINGS = ["with a warm surprise reunion", "with a clever shared solution", "with a funny misunderstanding cleared up",
           "with a gentle lesson about patience", "with a joyful community celebration",
           "with an unexpected act of kindness", "with a satisfying puzzle solved together",
           "with a proud moment of teamwork", "with a hopeful fresh start", "with a hearty shared laugh",
           "with a small act of courage paying off", "with a heartfelt thank-you from a neighbor",
           "with a last-minute save everyone cheers for", "with a quiet, touching surprise"]
ROLES = ["as traveling bakers", "as amateur inventors", "as neighborhood gardeners",
         "as weekend repair volunteers", "as small-town shopkeepers", "as curious explorers",
         "as youth sports coaches", "as community cooks", "as old friends on a road trip", "as art class partners",
         "as substitute schoolteachers", "as amateur photographers", "as weekend musicians",
         "as volunteer firefighters helping out", "as new librarians", "as summer camp counselors",
         "as amateur scientists", "as local delivery drivers", "as retired athletes turned coaches",
         "as first-time farmers", "as swim team volunteers", "as neighborhood tour guides"]


class ControlCenterError(ValueError):
    """Only application-owned messages, never raw Google/OpenAI payloads."""


def _parse(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def select_category(rnd, recent_categories):
    """One-click Recurring Cast fiction is deliberately locked to the Funny rubric — every
    auto-produced story's angle is a silly, punchy, laugh-driven scene, not genre variety.
    A human using the New Video form directly can still pick any other category there."""
    return "Funny"


def select_voice(rnd, previous_voice):
    candidates = [v for v in VOICES if v != previous_voice] or list(VOICES)
    return rnd.choice(candidates)


def select_visual_style(rnd, previous_style):
    candidates = [s for s in STYLES if s != previous_style] or list(STYLES)
    return rnd.choice(candidates)


def compose_idea(rnd, recent_fingerprints, trend_hint=None):
    """trend_hint, when given, is a real current topic's title used only as loose creative
    seasoning — never a claim that the fictional cast story is a real event. Mirrors the
    same fiction-only framing prompts.viral_direction already uses for the separate viral mode."""
    candidate = None
    for _ in range(12):
        location, obj = rnd.choice(LOCATIONS), rnd.choice(OBJECTS)
        ending, role = rnd.choice(ENDINGS), rnd.choice(ROLES)
        text = f"Set the story in {location}. A central object or problem: {obj}. The one or two chosen cast members appear {role}. End the story {ending}."
        if trend_hint:
            text += (f" For extra freshness, loosely draw creative inspiration from the general theme of this "
                     f"real current topic, fiction only, NOT a news report or a claim that this happened: {trend_hint}.")
        candidate = {"text": text, "location": location, "object": obj, "ending": ending, "role": role,
                     "trend_hint": trend_hint}
        if not any(similarity(text, fp) >= 0.72 for fp in recent_fingerprints):
            return candidate
    return candidate


def load_final_story(store, project_id):
    try:
        project = store.load(project_id)
        data = read_json(store.revision_dir(project) / "story.json")
    except (FileNotFoundError, KeyError):
        return None
    return StoryPackage.model_validate(data) if data else None


def recent_completed_projects(store, limit=10):
    rows = [r for r in store.history() if not r["legacy"] and r["status"] == "complete"]
    return sorted(rows, key=lambda r: r["date"], reverse=True)[:limit]


def fetch_trend_hint(trend_store, rnd):
    """Best-effort, read-only, free trend lookup for creative seasoning only.

    Reuses the exact same TrendStore the manual 'Viral' mode already uses (same cache,
    same free public RSS source, no paid request). Never blocks the one-click flow: any
    failure (offline, source unavailable, timeout) just means no trend hint this time.
    """
    if trend_store is None:
        return None
    try:
        snapshot = trend_store.lookup()
        records = snapshot.get("records") or []
        return rnd.choice(records[:5])["title"] if records else None
    except Exception:  # noqa: BLE001 - trend lookup is optional seasoning, never blocking
        return None


def select_creative_options(store, *, rnd=None, trend_store=None):
    """Everything this picks already exists in the dashboard: a real category, a real voice,
    a real visual style, and free-text placed in the existing 'idea' field."""
    seed = secrets.randbits(64)
    rnd = rnd or random.Random(seed)
    recent = recent_completed_projects(store)
    categories, voices, styles, fingerprints = [], [], [], []
    for row in recent:
        try:
            project = store.load(row["id"])
        except FileNotFoundError:
            continue
        categories.append(project.get("selected_category"))
        voices.append(project.get("options", {}).get("voice"))
        styles.append(project.get("options", {}).get("visual_style"))
        story = load_final_story(store, row["id"])
        if story and story.creative_fingerprint:
            fingerprints.append(" ".join(str(v) for v in story.creative_fingerprint.values()))
    category = select_category(rnd, categories)
    voice = select_voice(rnd, voices[0] if voices else None)
    visual_style = select_visual_style(rnd, styles[0] if styles else None)
    trend_hint = fetch_trend_hint(trend_store, rnd)
    idea = compose_idea(rnd, fingerprints, trend_hint=trend_hint)
    return {"seed": seed, "category": category, "voice": voice, "visual_style": visual_style,
            "narration_style": f"Very Easy English narration, matched to the {category} tone",
            "idea": idea, "previous_category": categories[0] if categories else None,
            "previous_voice": voices[0] if voices else None, "previous_visual_style": styles[0] if styles else None}


class ControlCenter:
    def __init__(self, settings, store: DashboardStore, youtube_studio: YouTubeDashboard, *,
                 trend_store=None, synchronous=False):
        self.settings, self.store, self.youtube_studio = settings, store, youtube_studio
        self.trend_store = trend_store
        self.synchronous = synchronous
        self.directory = settings.root / "data/control-center"
        if self.directory.is_symlink():
            raise ControlCenterError("Unsafe Control Center state directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "auto-runs").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        # Restart never starts or resumes work by itself; it only marks a mid-flight
        # run as interrupted, the same word DashboardStore already uses for its own
        # projects. The underlying project's own saved progress is untouched.
        for record in self._all_runs():
            if record.get("phase") not in TERMINAL_PHASES:
                record.update(phase="interrupted", updated_at=utc_now(),
                              message="Server stopped mid-run. The underlying project's saved progress is safe: "
                                      "resume it from Recent Projects, or start a new one-click run.")
                self._save_run(record)
        (self.directory / "active.json").unlink(missing_ok=True)

    # ---- persisted auto-run records -------------------------------------------------
    def _run_path(self, project_id):
        return self.directory / "auto-runs" / f"{project_id}.json"

    def _read_run(self, project_id):
        return read_json(self._run_path(project_id))

    def _save_run(self, record):
        save_json(self._run_path(record["project_id"]), record)

    def _update_run(self, project_id, **fields):
        with self.lock:
            record = self._read_run(project_id) or {"project_id": project_id}
            record.update(fields, updated_at=utc_now())
            self._save_run(record)
            return record

    def _all_runs(self):
        return [read_json(path) for path in (self.directory / "auto-runs").glob("*.json")]

    def active_project_id(self):
        pointer = read_json(self.directory / "active.json", {})
        project_id = pointer.get("project_id")
        if not project_id:
            return None
        record = self._read_run(project_id)
        if not record or record.get("phase") in TERMINAL_PHASES:
            return None
        return project_id

    def _set_active(self, project_id):
        save_json(self.directory / "active.json", {"project_id": project_id})

    def _clear_active(self, project_id):
        pointer = read_json(self.directory / "active.json", {})
        if pointer.get("project_id") == project_id:
            (self.directory / "active.json").unlink(missing_ok=True)

    # ---- statistics (cached, read-only, already-granted scope) ----------------------
    def channel_statistics(self, force=False):
        cache = read_json(self.directory / "channel-stats.json", {})
        fetched_at = _parse(cache.get("fetched_at"))
        fresh = fetched_at is not None and (datetime.now(UTC) - fetched_at) < timedelta(seconds=STATS_CACHE_SECONDS)
        if not force and fresh:
            return cache
        if not self.youtube_studio.status()["verified"]:
            return cache
        try:
            client = youtube.youtube_client(self.settings)
            stats = youtube.channel_statistics(client)
            video_ids = [r["video_id"] for r in self.youtube_studio.upload_history() if r.get("video_id")]
            videos = youtube.video_statistics(client, video_ids) if video_ids else {}
            record = {**stats, "videos": videos, "fetched_at": utc_now(), "error": None}
        except Exception:  # noqa: BLE001 - never leak provider payloads into the cache file
            record = {**cache, "error": "Could not refresh statistics from YouTube. Showing the last cached values."}
        save_json(self.directory / "channel-stats.json", record)
        return record

    # ---- local, non-API aggregates ---------------------------------------------------
    def channel_overview(self, history=None):
        rows = self.store.history() if history is None else history
        uploads = self.youtube_studio.upload_history()
        active = next((r for r in rows if not r["legacy"]
                      and r["status"] not in {"complete", "failed", "cancelled", "new"}), None)
        completed_uploads = [r for r in uploads if r["status"] == "complete"]
        last_upload = max(completed_uploads, key=lambda r: r.get("completed_at") or "", default=None)
        return {"generated_count": sum(1 for r in rows if r["status"] == "complete"),
                "uploaded_count": len(completed_uploads),
                "public_count": sum(1 for r in uploads if best_known_privacy(r) == "public"),
                "private_count": sum(1 for r in uploads if best_known_privacy(r) == "private"),
                "failed_count": sum(1 for r in rows if r["status"] == "failed"),
                "active_project": active, "last_upload": last_upload}

    def monthly_limits(self, history=None):
        rows = self.store.history() if history is None else history
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def in_month(value):
            parsed = _parse(value)
            return parsed is not None and parsed >= month_start
        this_month = [r for r in rows if not r["legacy"] and in_month(r["date"])]
        completed_count = sum(1 for r in this_month if r["status"] == "complete")
        failed_count = sum(1 for r in this_month if r["status"] == "failed")
        cancelled_count = sum(1 for r in this_month if r["status"] == "cancelled")
        spending = sum(r["cost"] or 0 for r in this_month)  # every project counts, including failed/cancelled ones
        uploaded_count = sum(1 for r in self.youtube_studio.upload_history()
                             if r.get("status") == "complete" and in_month(r.get("completed_at")))
        return {"completed_count": completed_count, "video_limit": MONTHLY_VIDEO_LIMIT,
                "uploaded_count": uploaded_count, "remaining_videos": max(0, MONTHLY_VIDEO_LIMIT - completed_count),
                "spending_usd": round(spending, 6), "spending_limit_usd": MONTHLY_SPEND_LIMIT_USD,
                "remaining_allowance_usd": round(max(0.0, MONTHLY_SPEND_LIMIT_USD - spending), 6),
                "average_cost_per_video": round(spending / completed_count, 6) if completed_count else None,
                "successful_count": completed_count, "failed_count": failed_count, "cancelled_count": cancelled_count,
                "video_warning": completed_count >= MONTHLY_VIDEO_WARNING,
                "spending_warning": spending >= MONTHLY_SPEND_WARNING_USD,
                "completed_hard_stop": completed_count >= MONTHLY_VIDEO_LIMIT,
                "spending_hard_stop": spending >= MONTHLY_SPEND_LIMIT_USD,
                "video_progress_pct": min(100, round(completed_count / MONTHLY_VIDEO_LIMIT * 100)),
                "spending_progress_pct": min(100, round(spending / MONTHLY_SPEND_LIMIT_USD * 100))}

    def recent_rows(self, sort="newest", history=None):
        rows = self.store.history() if history is None else history
        uploads_by_path = {record["path"]: record for record in self.youtube_studio.upload_history()}
        uploadable_by_directory = {v["directory"]: v["key"] for v in self.youtube_studio.videos()}
        video_stats = read_json(self.directory / "channel-stats.json", {}).get("videos", {})
        result = []
        for entry in rows:
            category = voice = visual_style = local_link = None
            resumable = False
            revision = 1
            if not entry["legacy"]:
                try:
                    project = self.store.load(entry["id"])
                except FileNotFoundError:
                    continue
                category, voice = project.get("selected_category"), project.get("options", {}).get("voice")
                visual_style = project.get("options", {}).get("visual_style")
                revision = project.get("revision", 1)
                local_link = f"/projects/{entry['id']}"
                resumable = project.get("status") in {"interrupted", "cancelled", "failed"} and bool(project.get("operation"))
            upload = uploads_by_path.get(str(Path(entry["output"]) / "final.mp4"))
            privacy = best_known_privacy(upload) if upload else None
            stats = video_stats.get(upload["video_id"]) if upload and upload.get("video_id") else None
            # A completed video with no upload attempt yet still needs a way in: it already
            # passed the same saved technical validation the manual YouTube page requires.
            upload_key = uploadable_by_directory.get(entry["output"]) if entry["status"] == "complete" and not upload else None
            result.append({"id": entry["id"], "legacy": entry["legacy"], "title": entry["title"],
                           "category": category, "voice": voice, "visual_style": visual_style,
                           "status": entry["status"], "created_at": entry["date"], "cost": entry["cost"],
                           "output": entry["output"], "resumable": resumable, "local_link": local_link,
                           "thumbnail": None if entry["legacy"] else f"/media/{entry['id']}/{revision}/scene-01.png",
                           "upload_status": upload["status"] if upload else None,
                           "upload_date": upload.get("completed_at") if upload else None, "privacy": privacy,
                           "video_id": upload.get("video_id") if upload else None,
                           "youtube_link": f"https://www.youtube.com/watch?v={upload['video_id']}"
                                          if upload and upload.get("video_id") else None,
                           "upload_link": f"/youtube/video/{upload_key}" if upload_key else None,
                           "views": (stats or {}).get("view_count"), "likes": (stats or {}).get("like_count"),
                           "comments": (stats or {}).get("comment_count")})
        filters = {"public": lambda r: r["privacy"] == "public", "private": lambda r: r["privacy"] == "private",
                   "failed": lambda r: r["status"] == "failed",
                   "in_progress": lambda r: r["status"] not in {"complete", "failed", "cancelled", "new"}}
        if sort in filters:
            result = [r for r in result if filters[sort](r)]
        keys = {"most_viewed": lambda r: r["views"] if r["views"] is not None else -1,
                "highest_cost": lambda r: r["cost"] if r["cost"] is not None else -1}
        result.sort(key=keys.get(sort, lambda r: r["created_at"] or ""), reverse=True)
        return result

    # ---- the one-click run itself ------------------------------------------------------
    def start(self, submission, *, privacy="public", video_mode="messi_ronaldo"):
        if privacy not in {"private", "public"}:
            raise ControlCenterError("Choose Private or Public before starting a one-click run.")
        if video_mode not in {"messi_ronaldo", "viral"}:
            raise ControlCenterError("Choose a valid video mode before starting a one-click run.")
        with self.lock:
            existing = next((r for r in self._all_runs() if r.get("submission") == submission), None)
            if existing:
                return existing["project_id"]  # exact duplicate submit: return the same run, start nothing new
            if self.active_project_id():
                raise ControlCenterError("A one-click project is already running. Wait for it to finish or fail.")
            limits = self.monthly_limits()
            if limits["completed_hard_stop"]:
                raise ControlCenterError(f"Monthly video limit reached ({MONTHLY_VIDEO_LIMIT} completed this month). No new project was started.")
            if limits["spending_hard_stop"]:
                raise ControlCenterError(f"Monthly spending limit reached (${MONTHLY_SPEND_LIMIT_USD:.2f} calculated this month). No new project was started.")
            if not self.youtube_studio.status()["verified"]:
                raise ControlCenterError(f"Connect and verify {youtube.EXPECTED_HANDLE} before one-click production.")
            if video_mode == "viral":
                # Viral Material already auto-picks its own niche/topic/category inside
                # DashboardStore.create() itself; there is nothing for select_creative_options
                # (a fiction-rubric/voice/visual-style/idea picker) to usefully add here.
                choice = None
                options = NewVideo(video_mode="viral", max_cost=PER_VIDEO_SPEND_LIMIT_USD)
            else:
                choice = select_creative_options(self.store, trend_store=self.trend_store)
                options = NewVideo(story_type=choice["category"], voice=choice["voice"],
                                    visual_style=choice["visual_style"], idea=choice["idea"]["text"],
                                    max_cost=PER_VIDEO_SPEND_LIMIT_USD)
            project_id = self.store.create(options, submission)
            self._save_run({"project_id": project_id, "submission": submission, "created_at": utc_now(),
                            "updated_at": utc_now(), "phase": "selecting", "selection": choice,
                            "video_mode": video_mode,
                            "audience": "general", "privacy": privacy, "cancel_requested": False,
                            "message": "Creative options selected before any paid request."})
            self._set_active(project_id)
        if self.synchronous:
            self._drive(project_id)
        else:
            worker = threading.Thread(target=self._drive, args=(project_id,), daemon=True, name="control-center-auto")
            worker.start()
        return project_id

    def _wait_idle(self, project_id, timeout=1800):
        deadline = time.monotonic() + timeout
        while True:
            if not self.store.load(project_id)["busy"]:
                return
            if time.monotonic() > deadline:
                raise ControlCenterError("Timed out waiting for the current stage to finish.")
            time.sleep(0.3)

    def _drive(self, project_id):
        try:
            self._update_run(project_id, phase="generating", message="Generating the story…")
            project = self.store.load(project_id)
            self.store.dispatch(project_id, "generate", project["version"])
            self._wait_idle(project_id)
            project = self.store.load(project_id)
            if project["status"] != "story_ready":
                self._update_run(project_id, phase="failed", message=f"Story generation did not complete: {project['message']}")
                return
            directory = self.store.revision_dir(project)
            evaluation = read_json(directory / "draft-evaluation.json", {})
            blocking = [w for w in evaluation.get("warnings", []) if any(marker in w for marker in STOP_WARNING_MARKERS)]
            if blocking:
                self._update_run(project_id, phase="failed", message="Stopped before automatic approval: " + " | ".join(blocking))
                return
            if (self._read_run(project_id) or {}).get("cancel_requested"):
                self._update_run(project_id, phase="cancelled", message="Cancelled before production started.")
                return
            self._update_run(project_id, phase="producing", message="Story approved automatically. Producing media…")
            self.store.dispatch(project_id, "approve", project["version"])
            self._wait_idle(project_id)
            project = self.store.load(project_id)
            if project["status"] != "complete":
                self._update_run(project_id, phase="failed", message=f"Production did not complete: {project['message']}")
                return
            if (self._read_run(project_id) or {}).get("cancel_requested"):
                self._update_run(project_id, phase="cancelled",
                                  message="Cancelled before upload started. The finished local video is preserved.")
                return
            self._update_run(project_id, phase="uploading", message="Preparing YouTube metadata…")
            self._upload(project_id)
        except Exception as error:  # noqa: BLE001 - never leak provider payloads into the saved record
            self._update_run(project_id, phase="failed", message=f"Stopped safely: {type(error).__name__}: {str(error)[:300]}")
        finally:
            self._clear_active(project_id)

    def _upload(self, project_id):
        project = self.store.load(project_id)
        directory = str(self.store.revision_dir(project))
        video = next((v for v in self.youtube_studio.videos() if v["directory"] == directory), None)
        if video is None:
            raise ControlCenterError("The completed video did not pass the YouTube uploader's own saved validation checks.")
        run = self._read_run(project_id) or {}
        preview = self.youtube_studio.preview(video["key"], run.get("audience", "general"), privacy=run.get("privacy", "private"))
        self._update_run(project_id, phase="uploading", message="Uploading to YouTube…",
                          title=preview["body"]["snippet"]["title"], description=preview["body"]["snippet"]["description"],
                          tags=preview["body"]["snippet"]["tags"], requested_privacy=preview["body"]["status"]["privacyStatus"],
                          channel=preview["channel"])
        self.youtube_studio.upload(preview["nonce"])
        record = next((r for r in self.youtube_studio.upload_history() if r["video_sha256"] == preview["video_sha256"]), None)
        if not record or record["status"] != "complete":
            self._update_run(project_id, phase="upload_failed",
                              message=(record or {}).get("message", "Upload outcome is uncertain. Check YouTube Studio."))
            return
        self._update_run(project_id, phase="checking_status", message="Checking the actual YouTube status…",
                          video_id=record["video_id"], returned_privacy=record.get("returned_privacy_status"))
        live_status = None
        try:
            live_status = self.youtube_studio.check_live_status(record["video_id"], secrets.token_hex(32))
        except youtube.YouTubeError:
            pass  # best-effort; the requested/returned status is already saved and shown honestly
        try:
            self.channel_statistics(force=True)
        except Exception:  # noqa: BLE001, S110 - statistics refresh is best-effort, never blocks completion
            pass
        self._update_run(project_id, phase="complete", message="Upload complete.", live_privacy=live_status)

    def cancel(self, project_id):
        self.store.cancel(project_id)  # reused: stops before the pipeline's next request, keeps completed assets
        self._update_run(project_id, cancel_requested=True,
                          message="Cancellation requested. Everything completed so far is preserved.")

    def public_run(self, project_id):
        run = self._read_run(project_id)
        if not run:
            raise ControlCenterError("Unknown one-click run")
        return {**run, "project": self.store.public(project_id), "studio_url": self.youtube_studio.studio_url(run["video_id"])
                if run.get("video_id") else None}


def register_control_center(app, settings, store, youtube_studio, *, trend_store=None, synchronous=False):
    center = ControlCenter(settings, store, youtube_studio, trend_store=trend_store, synchronous=synchronous)
    app.extensions["control_center"] = center
    routes = Blueprint("control_center", __name__)

    @routes.get("/control-center")
    def home():
        history = store.history()
        return render_template("control_center.html", y=youtube_studio.status(), stats=center.channel_statistics(),
                               overview=center.channel_overview(history), limits=center.monthly_limits(history),
                               active_project_id=center.active_project_id(),
                               rows=center.recent_rows(request.args.get("sort", "newest"), history),
                               sort=request.args.get("sort", "newest"), submission=secrets.token_hex(32),
                               nonce=secrets.token_hex(32))

    @routes.post("/control-center/refresh-statistics")
    def refresh_statistics():
        center.channel_statistics(force=True)
        return redirect("/control-center", code=303)

    @routes.post("/control-center/start")
    def start():
        project_id = center.start(request.form.get("submission", ""), privacy=request.form.get("privacy", "public"))
        return redirect(url_for("control_center.run", project_id=project_id), code=303)

    @routes.get("/control-center/run/<project_id>")
    def run(project_id):
        data = center.public_run(project_id)
        return render_template("control_center_run.html", r=data, p=data["project"], y=youtube_studio.status())

    @routes.get("/api/control-center/run/<project_id>")
    def run_api(project_id):
        return jsonify(center.public_run(project_id))

    @routes.post("/control-center/run/<project_id>/cancel")
    def cancel(project_id):
        center.cancel(project_id)
        return redirect(url_for("control_center.run", project_id=project_id), code=303)

    # ControlCenterError is a ValueError subclass; dashboard.py's existing generic
    # Exception handler already renders it as a friendly 400 with its own message.
    app.register_blueprint(routes)
    return center
