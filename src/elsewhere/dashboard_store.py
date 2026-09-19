"""Persistent browser workflow. Media production is delegated to Pipeline, not duplicated."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .api_diagnostics import LocalRequestError, public_diagnostic
from .config import Settings
from .costs import AmbiguousPaidRequest, BudgetExceeded, CostLedger, utc_now
from .disclosure import strip_disclosure, with_real_content_disclosure
from .languages import COUNTRIES, LANGUAGES, analyze_language, check_font
from .models import PublicFigureStoryResponse, StoryPackage
from .openai_service import AIService, save_json
from .performance import make_plan
from .pipeline import Pipeline
from .recurring_cast import CAST, DISCLOSURE, FLAGS, STYLE, enabled, with_disclosure
from .rubrics import (
    DEFAULT_CATEGORY,
    GLOBAL_SCORES,
    RUBRIC_VERSION,
    RUBRICS,
    STORY_TYPES,
    canonical_category,
    local_category_check,
)
from .safety import SafetyError, local_checks, similarity
from .scene_plan import narration_scenes, repair_scene_partition

LOG = logging.getLogger("elsewhere.dashboard_store")

STYLES = ["Cinematic realistic", "Illustrated cinematic", "Black-and-white noir", "Warm emotional cinema"]
VOICES = ["cedar", "marin", "alloy", "coral", "sage", "ash"]
DURATIONS = {"50-60": (50, 60), "52-60": (52, 60), "55-60": (55, 60)}
# Viral Material is deliberately locked to exactly this niche, not "anything trending" — and
# within it, VIRAL_NICHE_RULES (prompts.py) further locks the topic to GTA6 only.
VIRAL_NICHES = ("Gaming",)
# On-disk images/audio/video are the bulk of this app's disk use (a completed project's saved
# API responses duplicate each generated image as base64, on top of the image file itself); a
# small always-on server has room for barely more than a single day of output at 10 videos/day
# (learned the hard way: a 14-day window left the disk completely full within 2 days, silently
# stalling a cron run mid-request with no space left even to record the failure). Cleanup only
# ever removes the local copy of an already-finished (complete or failed) project older than
# this — never anything in progress, and never the YouTube upload itself, unaffected either way.
OUTPUT_RETENTION_DAYS = 1
STAGES = [("story_saved", "Story saved"),
          ("image_prompts", "Preparing image prompts")]
STAGES += [(f"image_{i:02d}", f"Image {i} of 8") for i in range(1, 9)]
STAGES += [("speech_generation", "Narration"), ("caption_alignment", "Word alignment"),
           ("captions", "Captions"), ("rendering", "Rendering"), ("validation", "Technical validation"),
           ("complete", "Complete")]
UNCERTAIN_BLOCKING = {"unknown_ambiguous_failure", "unknown_in_progress", "unknown_usage_unestimated"}


class Cancelled(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class NewVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    story_type: str = DEFAULT_CATEGORY
    duration: str = "50-60"
    visual_style: str = "Illustrated cinematic"
    voice: str = "cedar"
    idea: str = Field(default="", max_length=1000)
    max_cost: float = Field(default=.50, ge=.05, le=10)
    video_mode: str = "messi_ronaldo"
    country: str = "AUTO"  # Legacy field retained for saved forms; no country selection in the UI.
    language: str = "auto"
    trend_snapshot: str = Field(default="", max_length=32)
    trend_id: str = Field(default="", max_length=32)

    @field_validator("story_type", "duration", "visual_style", "voice", "video_mode", "country", "language")
    @classmethod
    def allowed_choices(cls, value, info):
        if info.field_name == "story_type":
            value = canonical_category(value)
        choices = {"story_type": STORY_TYPES, "duration": DURATIONS, "visual_style": STYLES, "voice": VOICES,
                   "video_mode": {"viral", "messi_ronaldo"}, "country": {*COUNTRIES, "AUTO"}, "language": {"auto", *LANGUAGES}}
        if value not in choices[info.field_name]:
            raise ValueError("Choose one of the displayed options")
        return value


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def safe_usage(value):
    """Only numeric counters reach the browser, never raw provider response/request data."""
    if isinstance(value, dict):
        return {k: safe_usage(v) for k, v in value.items()
                if k in {"input_tokens", "output_tokens", "total_tokens", "seconds", "cached_tokens",
                         "cache_write_tokens", "reasoning_tokens", "image_tokens", "text_tokens",
                         "input_tokens_details", "output_tokens_details", "measured_output_duration_seconds",
                         "estimated_input_text_tokens"}}
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class DashboardStore:
    def __init__(self, settings: Settings, *, service_factory=None, pipeline_factory=None, synchronous=False):
        self.base = settings
        self.output = settings.path("output").resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.service_factory = service_factory or AIService
        self.pipeline_factory = pipeline_factory or Pipeline
        self.synchronous = synchronous
        self.lock = threading.RLock()
        self.workers: set[threading.Thread] = set()
        self.stopping = False
        # Restart never starts work. Browser refresh/restart can only reveal saved state.
        for path in self.output.glob("dashboard-*/dashboard-project.json"):
            project = read_json(path)
            if project.get("status") == "complete":
                continue  # Completed projects are never migrated or rewritten.
            if "selected_category" not in project:
                requested = canonical_category(project["options"]["story_type"])
                project["selected_category"] = secrets.choice(list(RUBRICS)) if requested == "Random" else requested
                project["rubric_version"] = RUBRIC_VERSION
                save_json(path, project)
            if project.get("busy"):
                project.update(busy=False, status="interrupted", message="Server stopped. Saved work is retained; review costs before resuming.")
                project["version"] += 1
                for stage in project.get("stages", {}).values():
                    if stage["status"] == "running":
                        stage.update(status="failed", message="Interrupted; not automatically retried")
                save_json(path, project)

    def directory(self, project_id):
        if not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise FileNotFoundError("Unknown project")
        path = self.output / ("dashboard-" + project_id)
        if path.is_symlink() or not path.is_dir():
            raise FileNotFoundError("Unknown project")
        return path

    def load(self, project_id):
        with self.lock:
            project = read_json(self.directory(project_id) / "dashboard-project.json")
            if not project:
                raise FileNotFoundError("Unknown project")
            if "selected_category" not in project and project.get("status") == "complete":
                category = canonical_category(project["options"]["story_type"])
                project["selected_category"] = DEFAULT_CATEGORY if category == "Random" else category
            return project

    def save(self, project):
        project["updated_at"] = utc_now()
        save_json(self.directory(project["id"]) / "dashboard-project.json", project)

    def revision_dir(self, project, revision=None):
        number = int(revision or project["revision"])
        if number not in [draft["number"] for draft in project["drafts"]]:
            raise FileNotFoundError("Unknown story revision")
        path = self.directory(project["id"]) / f"revision-{number:04d}"
        if path.is_symlink():
            raise ValueError("Unsafe revision path")
        return path

    def new_revision(self, project, reason):
        number = project["revision"] + 1
        project["revision"] = number
        project["drafts"].append({"number": number, "created_at": utc_now(), "reason": reason})
        self.revision_dir(project).mkdir()
        if project.get("trend"):
            save_json(self.revision_dir(project) / "trend-source.json", project["trend"])
        project["stages"] = {}

    def select_niche(self):
        return VIRAL_NICHES[0]

    def create(self, options, submission):
        with self.lock:
            # Durable one-project-per-form protection (also after server restart).
            for path in self.output.glob("dashboard-*/dashboard-project.json"):
                old = read_json(path)
                if old.get("submission") == submission:
                    return old["id"]
            trend = None
            niche = None
            if options.video_mode == "viral":
                niche = self.select_niche()
                # A real trend is optional bonus seasoning now, not required: GTA6 is a locked
                # topic driven by the model's own real knowledge, not by whatever happens to
                # be trending globally on a given day.
                if options.trend_id:
                    from .trends import TrendStore
                    trend = TrendStore(self.base.root).selection(options.trend_snapshot, options.trend_id)
                    if options.country != "AUTO" and trend["country"] != options.country:
                        raise ValueError("The selected topic does not match the selected country")
            code = options.language if options.language != "auto" else (trend["suggested_language"] if trend else "en")
            check_font(code)
            project_id = secrets.token_hex(16)
            directory = self.output / ("dashboard-" + project_id)
            directory.mkdir(mode=0o700)
            project = {"id": project_id, "name": "Short " + project_id[:8], "created_at": utc_now(),
                       # Viral Material has no fixed rubric to pre-pick from: the model chooses its
                       # own real-content angle for the actual topic once generation completes, and
                       # this gets synced in afterward (see work()). An explicit Random pick still
                       # resolves immediately since fiction rubric categories are fixed in advance.
                       "selected_category": ("" if options.video_mode == "viral" else
                           secrets.choice(list(RUBRICS)) if options.story_type == "Random" else options.story_type),
                       "rubric_version": RUBRIC_VERSION,
                       "options": options.model_dump(), "submission": submission, "status": "new",
                       "revision": 0, "drafts": [], "stages": {}, "version": 1, "busy": False,
                       "cancel_requested": False, "operation": None, "message": "Ready to generate one story."}
            project.update(language=code, trend=trend, video_mode=options.video_mode, niche=niche)
            if enabled(self.base) and options.video_mode == "messi_ronaldo":
                project.update(recurring_cast={"version": 1, "main_characters": list(CAST)},
                               disclosure=DISCLOSURE, **FLAGS)
                project["recurring_cast"]["life_variety_version"] = self.base.raw["recurring_cast"].get("life_variety_version", 0)
            self.new_revision(project, "new")
            self.save(project)
            save_json(directory / "base-settings.json", self.base.raw)
            if trend:
                save_json(self.revision_dir(project) / "trend-source.json", trend)
                ledger = self.ledger(project_id, self.revision_dir(project) / "cost-report.json", options.max_cost, self.base.costs)
                ledger.add_local_stage("trend_research", "Public 24-hour trend snapshot; no paid AI research. " + trend["cost_basis"])
            self.report(project_id)
            return project_id

    def settings(self, project):
        raw = copy.deepcopy(read_json(self.directory(project["id"]) / "base-settings.json", self.base.raw))
        # New projects opt into the cast. Existing drafts and completed runs retain their contract.
        raw.pop("recurring_cast", None)
        if project.get("recurring_cast"):
            raw["recurring_cast"] = copy.deepcopy(project["recurring_cast"])
        options = project["options"]
        code = project.get("language", "en")
        raw["brand"]["language"] = code
        # Old 55-65 projects stay readable; unattempted production now caps at 60.
        minimum, maximum = DURATIONS.get(options["duration"], (55, 60))
        raw["formats"]["short"].update(duration_min_seconds=minimum, duration_max_seconds=maximum,
            target_seconds=(minimum + maximum) / 2, narration_words_min=115, narration_words_max=135, scene_count=8)
        raw["models"]["voice"] = options["voice"]
        raw["models"]["voice_instructions"] = (
            f"Use very simple conversational English with natural sentence variation. "
            f"Match the tone of {project['selected_category']}; do not add other genre effects. "
            f"Read the exact text, with a complete ending. Aim for {minimum}-{maximum} seconds, "
            "with meaningful pauses, never robotic or hurried."
        )
        if code != "en":
            raw["models"]["voice_instructions"] = raw["models"]["voice_instructions"].replace("English", LANGUAGES[code]) + " Speak only the exact native-script input, never translate it into English."
            raw["formats"]["short"]["caption_words"] = 4
        raw["brand"]["visual_style"] = options["visual_style"] + "; no text, logos, real people, or copyrighted characters"
        if enabled(Settings(self.base.root, raw)):
            raw["models"]["speech"] = "gpt-4o-mini-tts"
            raw["brand"]["visual_style"] = STYLE + "; palette/mood: " + options["visual_style"]
            raw["safety"]["forbidden_topics"] = [t for t in raw["safety"]["forbidden_topics"] if t != "real living people"]
            raw["costs"]["request_reserves_usd"]["story"] = .05  # Longer cast/continuity instructions and history.
        raw["dashboard_brief"] = {"story_type": options["story_type"], "selected_category": project["selected_category"],
                                  "rubric_version": RUBRIC_VERSION, "additional_idea": options["idea"]}
        if project.get("video_mode") == "viral":
            raw["dashboard_brief"].update(video_mode="viral", trend=project.get("trend"), niche=project.get("niche"))
            raw["costs"]["request_reserves_usd"]["story"] = .05
            # Real content may legitimately show a real person (a footballer, a real-story
            # subject) — never literal game logos or copyrighted character/UI designs though.
            raw["brand"]["visual_style"] = options["visual_style"] + "; no text, logos, or copyrighted characters"
            raw["safety"]["forbidden_topics"] = [t for t in raw["safety"]["forbidden_topics"] if t != "real living people"]
            # blocked_terms exists to stop the FICTION engine writing disguised franchise
            # fan-fiction (a story secretly about Minecraft/Pokemon). The Gaming niche's entire
            # point is real, factual commentary naming real games, so that reasoning no longer
            # applies; keep blocking unrelated, politically sensitive named individuals though.
            franchise_terms = {"Harry Potter", "Marvel", "DC Comics", "Star Wars", "Disney", "Pokemon", "Minecraft"}
            raw["safety"]["blocked_terms"] = [t for t in raw["safety"]["blocked_terms"] if t not in franchise_terms]
        raw["safety"].update(max_generation_attempts=0, max_review_attempts=1, max_sdk_retries=0)
        raw["costs"]["default_allowance_usd"] = options["max_cost"]
        raw["costs"]["request_reserves_usd"]["image"] = .045
        return Settings(self.base.root, raw)

    def report(self, project_id):
        with self.lock:
            project = self.load(project_id)
            requests = []
            for draft in project["drafts"]:
                data = read_json(self.revision_dir(project, draft["number"]) / "cost-report.json", {})
                requests += [dict(item, revision=draft["number"]) for item in data.get("requests", [])]
                diagnostic = read_json(self.revision_dir(project, draft["number"]) /
                                       "image-diagnostic-0001" / "cost-report.json", {})
                requests += [dict(item, revision=draft["number"], diagnostic_attempt=True)
                             for item in diagnostic.get("requests", [])]
            total = sum(item.get("calculated_cost_usd") or 0 for item in requests)
            result = {"allowance_usd": project["options"]["max_cost"], "calculated_estimate_usd": round(total, 8),
                      "remaining_usd": round(max(0, project["options"]["max_cost"] - total), 8),
                      "confirmed_billing_usd": None, "requests": requests,
                      "unknown_charges": sum(str(r.get("charge_status", "")).startswith("unknown") for r in requests),
                      "blocked_by_uncertainty": any(r.get("charge_status") in UNCERTAIN_BLOCKING for r in requests),
                      "updated_at": utc_now()}
            path = self.directory(project_id) / "cost-report.json"
            previous = read_json(path, {})
            if {k: v for k, v in previous.items() if k != "updated_at"} == {k: v for k, v in result.items() if k != "updated_at"}:
                return previous  # A refresh must not rewrite an unchanged cost record.
            if project["status"] != "complete":
                save_json(path, result)
            return result

    def check_cancel(self, project_id):
        if self.stopping or self.load(project_id).get("cancel_requested"):
            raise Cancelled("Cancelled. Completed files are retained; no next request was started.")

    def guard(self, project_id, reserve, stage):
        if stage == "quality_review":
            raise ValueError("Paid story review is disabled: your approval is final")
        self.check_cancel(project_id)
        report = self.report(project_id)
        if report["blocked_by_uncertainty"]:
            raise BudgetExceeded("An earlier charge is uncertain. Further paid work is blocked; check billing first.")
        if reserve > report["remaining_usd"] + 1e-9:
            raise BudgetExceeded(f"The next request needs a ${reserve:.3f} reserve; ${report['remaining_usd']:.3f} remains.")

    def ledger(self, project_id, path, allowance, pricing):
        return CostLedger(path, allowance, pricing,
                          before_request=lambda reserve, stage: self.guard(project_id, reserve, stage),
                          on_change=lambda _: self.report(project_id))

    def event(self, project_id, stage, status, message=""):
        with self.lock:
            project = self.load(project_id)
            record = project["stages"].setdefault(stage, {"started_at": None, "completed_at": None})
            record.update(status=status, message=message)
            record["started_at"] = record["started_at"] or utc_now()
            if status in {"completed", "failed"}:
                record["completed_at"] = utc_now()
            project["message"] = message
            self.save(project)

    def prior_stories(self, project):
        stories = []
        for path in self.output.glob("dashboard-*/revision-*/draft.json"):
            if path != self.revision_dir(project) / "draft.json":
                stories.append(StoryPackage.model_validate_json(path.read_text()))
        for path in self.output.glob("*/story.json"):
            try:
                stories.append(StoryPackage.model_validate_json(path.read_text()))
            except ValueError:
                continue
        return stories[-100:]

    def evaluate(self, story, project, *, compare=True):
        warnings = []
        errors = []
        try:
            local_checks(story, self.settings(project), [], expected_format="short", advisory_editorial=True)
        except (SafetyError, ValueError) as error:
            errors.append(str(error))
        try:
            local_checks(story, self.settings(project), [], expected_format="short")
        except (SafetyError, ValueError) as error:
            if str(error) not in errors:
                warnings.append("Advisory only — your approval overrides story targets: " + str(error))
        if compare:
            if not story.continuity_bible or any(not story.creative_fingerprint.get(k) for k in ("main_object", "setting", "characters", "twist")):
                warnings.append("Missing continuity or originality descriptors; fresh visual planning will use your approved text.")
            # Viral Material is locked to one real topic (GTA6): its real-world setting, and
            # often its named characters (Lucia/Jason), are legitimately the same story to
            # story — that repetition is an unavoidable fact about the topic, not a sign of a
            # lazily-reused idea, so it must not trip this check the way it would for fiction.
            viral_locked_fields = {"setting", "characters"} if project.get("video_mode") == "viral" else set()
            for previous in self.prior_stories(project):
                repeated = [key for key, value in story.creative_fingerprint.items()
                            if not (project.get("recurring_cast") and key == "characters")
                            if key not in viral_locked_fields
                            if value and previous.creative_fingerprint.get(key)
                            and similarity(value, previous.creative_fingerprint[key]) >= .72]
                if similarity(story.narration, previous.narration) >= .70 or repeated:
                    warnings.append("Too similar to an earlier story" + (": " + ", ".join(repeated) if repeated else ""))
                    break
        easy = analyze_language(story.narration, project.get("language", "en"))
        category = local_category_check(story.narration, project["selected_category"])
        if project.get("language", "en") != "en":
            category = {"errors": [], "evidence": [], "score_cap": None}
            warnings.append("Local semantic/category screening is English-only. Native-language category match and content need your inspection; author scores are not independent verification.")
        scores = {key: value for key, value in story.draft_scores.items()
                  if key in GLOBAL_SCORES and isinstance(value, int) and 1 <= value <= 10}
        # Viral Material has no fixed rubric to filter category_scores against; accept the
        # model's own self-assessed scores for whatever real-content angle it chose.
        category_scores = ({s.name: s.score for s in story.draft_category_scores} if project.get("video_mode") == "viral"
                           else {s.name: s.score for s in story.draft_category_scores
                                if project["selected_category"] in RUBRICS and s.name in RUBRICS[project["selected_category"]][1]})
        if category["score_cap"] is not None:
            scores["category_match"] = min(scores.get("category_match", 10), category["score_cap"])
            for key in ("investigation", "consequence"):
                category_scores[key] = min(category_scores.get(key, 10), category["score_cap"])
        return {"readability": easy["metrics"], "very_easy_english": easy, "warnings": warnings, "errors": errors,
                "scene_count": len(story.scenes), "local_category_check": category,
                "rubric_version": RUBRIC_VERSION, "selected_category": project["selected_category"],
                "estimated_narration_seconds": easy["estimated_seconds"],
                "draft_scores": scores, "draft_category_scores": category_scores}

    def claim(self, project, version):
        if project["busy"] or int(version) != project["version"]:
            raise Conflict("This action was already submitted or the page is stale. Refresh to see saved progress.")
        project["version"] += 1

    def dispatch(self, project_id, action, version):
        with self.lock:
            project = self.load(project_id)
            self.claim(project, version)
            if action == "generate":
                if project["status"] == "complete":
                    raise Conflict("This video is complete. Start a new project.")
                # A new click is explicit additional spending, never an automatic retry.
                report = self.report(project_id)
                if report["blocked_by_uncertainty"]:
                    raise BudgetExceeded("Uncertain earlier charge: new requests are blocked.")
                if (self.revision_dir(project) / "draft.json").exists() or project["operation"]:
                    self.new_revision(project, "generated_another")
                if enabled(self.base) and not project.get("recurring_cast") and project.get("video_mode", "messi_ronaldo") != "viral":
                    project.update(recurring_cast={"version": 1, "main_characters": list(CAST)},
                                   disclosure=DISCLOSURE, **FLAGS)
                if project.get("recurring_cast"):
                    project["recurring_cast"]["life_variety_version"] = self.base.raw.get("recurring_cast", {}).get("life_variety_version", 0)
                operation = "generate"
            elif action == "approve":
                if project["status"] not in {"story_ready", "review_rejected"}:
                    raise Conflict("Edit or generate a story before approving it.")
                directory = self.revision_dir(project)
                story = StoryPackage.model_validate_json((directory / "draft.json").read_text())
                evaluation = self.evaluate(story, project, compare=project["drafts"][-1]["reason"] != "manual_edit")
                save_json(directory / "draft-evaluation.json", evaluation)
                if evaluation.get("errors"):
                    raise ValueError("Resolve the local validation errors before creating this video.")
                approval_path = directory / "human-approval.json"
                if approval_path.exists():
                    # Preserve the original review-era consent as history.
                    archive = directory / "approval-history"
                    archive.mkdir(exist_ok=True)
                    save_json(archive / (secrets.token_hex(16) + ".json"), read_json(approval_path))
                save_json(approval_path, {"approved_at": utc_now(), "revision": project["revision"],
                          "selected_category": project["selected_category"], "rubric_version": RUBRIC_VERSION,
                          "final_approval": True, "story_sha256": hashlib.sha256(story.model_dump_json().encode()).hexdigest()})
                operation = "produce"
            elif action == "resume":
                if project["status"] not in {"interrupted", "cancelled", "failed"}:
                    raise Conflict("Use Create Video From This Story to give final approval, or edit the story first.")
                if self.report(project_id)["blocked_by_uncertainty"]:
                    raise BudgetExceeded("Uncertain request charge: resume cannot safely make more paid requests.")
                operation = project["operation"]
                if operation not in {"generate", "produce"}:
                    raise Conflict("Nothing is pending. Generate or edit a story.")
            else:
                raise ValueError("Unknown action")
            project.update(busy=True, status="generating" if operation == "generate" else "producing",
                           operation=operation, cancel_requested=False, message="Starting your requested action…")
            self.save(project)
        if self.synchronous:
            self.work(project_id, operation)
        else:
            worker = threading.Thread(target=self.work, args=(project_id, operation), name="short-work", daemon=False)
            self.workers.add(worker)
            worker.start()

    def work(self, project_id, operation):
        try:
            project = self.load(project_id)
            directory = self.revision_dir(project)
            settings = self.settings(project)
            self.check_cancel(project_id)
            if operation == "generate":
                ledger = self.ledger(project_id, directory / "cost-report.json", project["options"]["max_cost"], settings.costs)
                draft = directory / "draft.json"
                if not draft.exists():
                    if any(r.get("stage") == "story_generation" for r in ledger.requests):
                        # Recover the structured story from an immediately saved response, never repeat a request.
                        archives = sorted((directory / "api-responses").glob("*-story_generation.json"))
                        recovered = None
                        for archive in archives:
                            for output in read_json(archive).get("output", []):
                                for content in output.get("content", []):
                                    if content.get("type") == "output_text":
                                        payload = json.loads(content["text"])
                                        if enabled(settings):
                                            # Historical archives predate the separate narration field.
                                            payload.setdefault("narration", " ".join(s["narration"] for s in payload.get("scenes", [])))
                                            parsed = PublicFigureStoryResponse.model_validate(payload)
                                            save_json(directory / "cast-manifest.json", {"named_characters": parsed.named_characters,
                                                      "speaking_characters": parsed.speaking_characters})
                                            payload.pop("named_characters")
                                            payload.pop("speaking_characters")
                                        recovered = StoryPackage.model_validate(payload)
                        if recovered is None:
                            raise ValueError("The story request was already attempted but no usable story was saved. It will not be repeated.")
                        story = recovered
                    else:
                        self.event(project_id, "story_generation", "running", "Generating exactly one story; no media is being created")
                        previous = [json.dumps({"title": s.title, "premise": s.premise,
                                   "avoid": s.creative_fingerprint}, ensure_ascii=False) for s in self.prior_stories(project)]
                        story = self.service_factory(settings, ledger).create_story("short", previous)
                    if enabled(settings):
                        story = with_disclosure(story)
                    elif project.get("video_mode") == "viral":
                        story = with_real_content_disclosure(story)
                    try:
                        story = repair_scene_partition(story)
                    except ValueError:
                        pass  # Save an unusable draft for local validation; never buy a replacement.
                    if project.get("video_mode") == "viral" and story.story_category:
                        # Sync the model's own chosen real-content angle back onto the project
                        # BEFORE the narration plan below is computed and saved, so the plan
                        # locked in now already reflects the final category. create_speech()
                        # later recomputes this exact plan from current settings and rejects a
                        # mismatch as tampering — syncing after saving would falsely trip that.
                        with self.lock:
                            fresh = self.load(project_id)
                            fresh["selected_category"] = story.story_category
                            self.save(fresh)
                        project["selected_category"] = story.story_category
                        settings = self.settings(project)
                    save_json(draft, story)
                    if enabled(settings) or project.get("video_mode") == "viral":
                        save_json(directory / "narration-performance-plan.json", make_plan(story.narration, settings))
                story = StoryPackage.model_validate_json(draft.read_text())
                evaluation = self.evaluate(story, project)
                save_json(directory / "draft-evaluation.json", evaluation)
                self.event(project_id, "story_generation", "completed", "Story ready. No images or speech generated.")
                status = "story_ready"
                message = ("Story saved. A safety or file-structure problem needs attention before production; editorial targets are advisory."
                           if evaluation["errors"] else
                           "Read or edit your story. Your approval is final. Production will begin immediately.")
            else:
                if not (directory / "human-approval.json").exists():
                    raise ValueError("Manual story approval is required before production")
                pipeline = self.pipeline_factory(settings, service_factory=self.service_factory,
                    ledger_factory=lambda path, allowance, pricing: self.ledger(project_id, path, allowance, pricing),
                    progress=lambda *args: self.event(project_id, *args),
                    check_cancel=lambda: self.check_cancel(project_id))
                pipeline.run("short", resume=directory, story_file=directory / "draft.json",
                             allowance_usd=project["options"]["max_cost"], paid_approved=True, human_approved=True)
                status, message = "complete", "Your local video is ready. Nothing has been uploaded."
            with self.lock:
                project = self.load(project_id)
                # Persist the final aggregate once, before completed projects become read-only.
                self.report(project_id)
                project.update(status=status, message=message, busy=False)
                self.save(project)
        except Exception as error:  # noqa: BLE001 - worker errors must never expose credentials or SDK payloads
            try:
                with self.lock:
                    project = self.load(project_id)
                    status = "cancelled" if isinstance(error, Cancelled) else "failed"
                    if isinstance(error, (Cancelled, BudgetExceeded, AmbiguousPaidRequest, SafetyError, LocalRequestError)):
                        message = str(error)
                    elif isinstance(error, ValueError):
                        message = "Local validation could not complete. Saved files are retained. Inspect local checks and duration before resuming."
                    else:
                        message = "The operation stopped safely. No automatic retry was started; saved files and costs are retained."
                    project.update(status=status, busy=False, message=message)
                    for stage in project["stages"].values():
                        if stage["status"] == "running":
                            stage.update(status="failed", completed_at=utc_now(), message=message)
                    self.save(project)
            except Exception as save_error:  # noqa: BLE001 - recording this recovery failure must never itself go uncaught
                # If even saving the failure state fails (for example the disk was full), the
                # project is left stuck at busy=True with no further trace otherwise — log the
                # exception type only (never message/args, which may hold request content) so
                # this is at least diagnosable instead of a silently dead worker thread.
                LOG.critical("work(): could not record failure for project %s after %s: %s",
                            project_id, type(error).__name__, type(save_error).__name__)
        finally:
            self.report(project_id)

    def cancel(self, project_id):
        with self.lock:
            project = self.load(project_id)
            if project["busy"]:
                project.update(cancel_requested=True, message="Cancellation requested. An accepted request may finish; no next request will start.")
                self.save(project)

    def edit(self, project_id, version, title, script):
        script = strip_disclosure(script)
        if not 8 <= len(title.strip()) <= 90 or not 80 <= len(script) <= 4000:
            raise ValueError("Use an 8–90 character title and an 80–4000 character script.")
        words = script.split()
        if len(words) < 32:
            raise ValueError("The script needs enough words for eight scenes.")
        with self.lock:
            project = self.load(project_id)
            self.claim(project, version)
            if project["status"] == "complete":
                raise Conflict("Completed videos are immutable. Start a new project.")
            original = StoryPackage.model_validate_json((self.revision_dir(project) / "draft.json").read_text())
            # Keep the exact words; only distribute them across eight editable scene records.
            # No cast, setting, action, or twist directions survive a manual edit.
            # These are narration-only placeholders, never production image prompts.
            scenes = narration_scenes(" ".join(words))
            edited = original.model_copy(update={"title": title.strip(), "hook": scenes[0].narration, "scenes": scenes,
                                                 "narration": " ".join(words),
                                                 "premise": "Manually edited story: " + " ".join(words)[:350],
                                                 "description": "Manually edited story: " + " ".join(words)[:950],
                                                 "hashtags": ["#OriginalStory", "#ShortStory"],
                                                 "continuity_bible": "", "creative_fingerprint": {}, "draft_scores": {},
                                                 "story_category": project["selected_category"], "draft_category_scores": []})
            edited = StoryPackage.model_validate(edited.model_dump())
            if project.get("recurring_cast"):
                edited = with_disclosure(edited)
            self.new_revision(project, "manual_edit")
            save_json(self.revision_dir(project) / "draft.json", edited)
            if project.get("recurring_cast") or project.get("video_mode") == "viral":
                save_json(self.revision_dir(project) / "narration-performance-plan.json", make_plan(edited.narration, self.settings(project)))
            evaluation = self.evaluate(edited, project, compare=False)
            save_json(self.revision_dir(project) / "draft-evaluation.json", evaluation)
            project.update(status="story_ready", operation=None,
                           message="Edits saved for free. Resolve the local check problems shown below."
                           if evaluation["errors"] else "Edits saved. Local checks updated. Your approval is final.")
            self.save(project)

    def reject(self, project_id, version):
        with self.lock:
            project = self.load(project_id)
            self.claim(project, version)
            if project["status"] == "complete":
                raise Conflict("Completed videos cannot be rejected or deleted here.")
            project.update(status="story_rejected", message="Story rejected and preserved in history.")
            self.save(project)

    def public(self, project_id):
        project = self.load(project_id)
        directory = self.revision_dir(project)
        report = self.report(project_id)
        requests = [{k: item.get(k) for k in ("stage", "revision", "status", "model_requested", "model_returned",
                                             "calculated_cost_usd", "charge_status", "started_at", "completed_at")}
                    | {"usage": safe_usage(item.get("usage")),
                       "diagnostic": public_diagnostic(item) if item.get("status") == "failed" else None}
                    for item in report["requests"]]
        stages = []
        for key, label in STAGES:
            record = dict(project["stages"].get(key, {}))
            paid = [r for r in requests if r["stage"] == key and r["revision"] == project["revision"]]
            if paid:
                last = paid[-1]
                # A succeeded request is not proof that the asset/local validation finished.
                record.setdefault("status", "running" if last["status"] == "in_progress" else "completed" if last["status"] == "succeeded" else "failed")
                record.setdefault("started_at", last["started_at"])
                record.setdefault("completed_at", last["completed_at"])
            costs = [r["calculated_cost_usd"] for r in paid]
            stages.append({"key": key, "label": label, "status": record.get("status", "waiting"),
                           "started_at": record.get("started_at"), "completed_at": record.get("completed_at"),
                           "cost": None if any(c is None for c in costs) else round(sum(costs), 8),
                           "message": record.get("message", "Waiting")})
        draft = read_json(directory / "draft.json")
        evaluation = (self.evaluate(StoryPackage.model_validate(draft), project,
                                   compare=project["drafts"][-1]["reason"] != "manual_edit") if draft else {})
        if draft:
            parsed = StoryPackage.model_validate(draft)
            draft = {"title": parsed.title, "narration": parsed.narration,
                     "youtube_disclosure": parsed.youtube_disclosure,
                     "scenes": [{"narration": s.narration} for s in parsed.scenes]}
        review = read_json(directory / "review.json")
        story_requests = [r for r in requests if r["stage"] == "story_generation" and r["revision"] == project["revision"]]
        generation_cost = (None if any(r["calculated_cost_usd"] is None for r in story_requests)
                           else sum(r["calculated_cost_usd"] for r in story_requests))
        completed = sum(s["status"] == "completed" for s in stages)
        remaining = sum(.015 for i in range(1, 9) if not (directory / f"scene-{i:02d}.png").exists())
        remaining += .02 if not (directory / "narration-source.wav").exists() else 0
        remaining += .006 if not (directory / "alignment.json").exists() else 0
        remaining += .008 if not (directory / "visual-plan.json").exists() else 0
        return {"id": project_id, "name": project["name"],
                "video_mode": project.get("video_mode", "messi_ronaldo"),
                "language": project.get("language", "en"), "language_name": LANGUAGES[project.get("language", "en")],
                "trend": project.get("trend"), "niche": project.get("niche"),
                "status": "story_ready" if project["status"] == "review_rejected" else project["status"], "busy": project["busy"],
                "selected_category": project["selected_category"], "global_score_labels": GLOBAL_SCORES,
                "recurring_cast": project.get("recurring_cast"), "disclosure": project.get("disclosure", ""),
                "synthetic_metadata": {key: project.get(key, False) for key in FLAGS},
                # Viral Material has no fixed rubric: its "category" is a real-content angle the
                # model chooses for itself, not a key into RUBRICS.
                "category_score_labels": {} if project["selected_category"] not in RUBRICS else RUBRICS[project["selected_category"]][1],
                "category_rules": ("Real, factual content in whichever real-content angle best fits the actual topic; no fixed rubric."
                                   if project["selected_category"] not in RUBRICS else RUBRICS[project["selected_category"]][0]),
                "review_category_scores": {s["name"]: s["score"] for s in (review or {}).get("category_specific_scores", [])},
                "message": ("Your approval is final. Production will begin immediately. The old review is history only."
                            if project["status"] == "review_rejected" else project["message"]),
                "version": project["version"], "revision": project["revision"],
                "created_at": project["created_at"], "options": {k: v for k, v in project["options"].items() if k != "idea"}, "drafts": project["drafts"],
                "story": draft, "evaluation": evaluation,
                "review": review, "generation_cost": generation_cost,
                "validation": read_json(directory / "validation.json"),
                "stages": stages, "progress": round(completed / len(stages) * 100),
                "calculated_cost": report["calculated_estimate_usd"], "remaining_allowance": report["remaining_usd"],
                "uncertain_charges": report["unknown_charges"], "cost_blocked": report["blocked_by_uncertainty"],
                "estimated_remaining": round(remaining, 4), "requests": requests,
                "output_directory": str(directory), "cost_report_path": str(self.directory(project_id) / "cost-report.json"),
                "images": [f"scene-{i:02d}.png" for i in range(1, 9) if (directory / f"scene-{i:02d}.png").exists()],
                "video": (directory / "final.mp4").exists(),
                "files": [str(p) for p in sorted(directory.iterdir()) if p.is_file() and p.name in
                          {"final.mp4", "story.json", "draft.json", "captions.srt", "alignment.json", "narration.wav", "validation.json", "review.json", "visual-plan.json", "cost-report.json", "narration-performance-plan.json", "narration-instructions.json", "production-metadata.json", "image-prompts.json", "trend-source.json"}]}

    def cleanup_old_output(self, days=OUTPUT_RETENTION_DAYS):
        """Delete the on-disk output directory (images, audio, video, saved API responses —
        every byte under output/dashboard-<id>/) for any finished project older than `days`.
        Never touches a project that's currently generating, and never touches anything on
        YouTube: an already-completed upload is entirely unaffected by removing the local
        copy. Returns the list of removed project ids."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed = []
        for path in self.output.glob("dashboard-*/dashboard-project.json"):
            project = read_json(path, {})
            if project.get("busy") or project.get("status") not in {"complete", "failed"}:
                continue
            created_at = project.get("created_at")
            if not created_at:
                continue
            if datetime.fromisoformat(created_at) < cutoff:
                shutil.rmtree(path.parent, ignore_errors=True)
                removed.append(project.get("id"))
        return removed

    def history(self):
        rows = []
        for path in self.output.glob("dashboard-*/dashboard-project.json"):
            project = read_json(path)
            public = self.public(project["id"])
            rows.append({"id": project["id"], "legacy": False, "name": project["name"],
                         "title": (public["story"] or {}).get("title", "No story yet"),
                         "type": public["selected_category"] + (" (Random)" if project["options"]["story_type"] == "Random" else ""),
                         "date": project["created_at"], "status": project["status"], "cost": public["calculated_cost"],
                         "output": public["output_directory"]})
        # Existing CLI outputs are read-only; none are rewritten or automatically resumed.
        for path in self.output.glob("*/run-state.json"):
            state = read_json(path)
            story = read_json(path.parent / "story.json", {})
            cost = read_json(path.parent / "cost-report.json", {}).get("summary", {}).get("calculated_estimate_usd")
            rows.append({"id": path.parent.name, "legacy": True, "name": "Existing local run",
                         "title": story.get("title", "No saved story"), "type": "Existing prototype",
                         "date": state.get("created_at", ""), "status": state.get("status", "unknown"),
                         "cost": cost, "output": str(path.parent)})
        return sorted(rows, key=lambda r: r["date"], reverse=True)

    def shutdown(self):
        self.stopping = True
        # Accepted requests may finish and persist. No new request passes check_cancel.
        for worker in list(self.workers):
            worker.join()
