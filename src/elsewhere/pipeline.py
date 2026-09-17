from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from .config import Settings
from .costs import AmbiguousPaidRequest, CostLedger
from .database import ContentStore
from .demo import (
    create_demo_images,
    curated_story,
    demo_review,
    demo_story,
    eighth_shadow_final_story,
    eighth_shadow_story,
)
from .disclosure import require_content_only, youtube_description
from .models import NarrationAlignment, StoryPackage, StoryReview, VisualPlan
from .openai_service import AIService, save_json
from .performance import make_plan
from .recurring_cast import DISCLOSURE, FLAGS, contract, enabled
from .renderer import (
    create_local_narration,
    media_duration,
    normalize_narration,
    render_video,
    synthetic_alignment,
    validate_video,
)
from .safety import SafetyError, local_checks, review_checks


def _load_model(path: Path, model):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _valid_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.width >= 1000 and image.height >= 1500
    except (OSError, SyntaxError, ValueError):
        return False


class Pipeline:
    def __init__(self, settings: Settings, *, service_factory=None, ledger_factory=None,
                 progress=None, check_cancel=None):
        self.settings = settings
        self.service_factory = service_factory or AIService
        self.ledger_factory = ledger_factory or CostLedger
        self.progress = progress
        self.check_cancel = check_cancel

    def emit(self, stage, status, message=""):
        if status == "running" and self.check_cancel:
            self.check_cancel()
        if self.progress:
            self.progress(stage, status, message)

    def _new_job_dir(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        job_dir = self.settings.path("output") / f"{stamp}-one-video-test"
        job_dir.mkdir(parents=True, exist_ok=False)
        return job_dir

    def run(
        self,
        format_name: str,
        dry_run: bool = False,
        upload: bool = False,
        privacy: str = "private",
        *,
        resume: Path | None = None,
        fixture: str | None = None,
        allowance_usd: float = 2.0,
        paid_approved: bool = False,
        story_file: Path | None = None,
        review_only: bool = False,
        human_approved: bool = False,
    ) -> Path:
        del privacy
        if upload:
            raise ValueError("The one-video test workflow never uploads")
        if format_name != "short":
            raise ValueError("This test workflow requires --format short")
        if fixture not in (
            None,
            "mirror",
            "matter-printer",
            "matter-printer-final",
            "eighth-shadow",
            "eighth-shadow-final",
        ):
            raise ValueError(f"Unknown story fixture: {fixture}")
        if not dry_run and not paid_approved:
            raise ValueError("Paid calls require the explicit --approve-paid flag")
        if story_file and (fixture or dry_run):
            raise ValueError("A supplied exact story cannot be combined with a fixture or dry run")
        if human_approved and (dry_run or review_only or not story_file):
            raise ValueError("Final human approval requires an exact saved story and media production")

        job_dir = resume.resolve() if resume else self._new_job_dir()
        if resume and not job_dir.is_dir():
            raise FileNotFoundError(f"Resume directory does not exist: {job_dir}")
        ledger = self.ledger_factory(job_dir / "cost-report.json", allowance_usd, self.settings.costs)
        state_path = job_dir / "run-state.json"
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {
                "schema_version": 1,
                "format": format_name,
                "dry_run": dry_run,
                "fixture": fixture,
                "fixed_story": story_file is not None,
                "content_id": None,
                "created_at": datetime.now(UTC).isoformat(),
                "status": "started",
            }
        )
        if state["format"] != format_name or bool(state["dry_run"]) != dry_run:
            raise ValueError("Resume arguments do not match the original run")

        def save_state(status: str, **fields) -> None:
            state.update(fields)
            state["status"] = status
            if status != "failed":
                state.pop("error", None)
            state["updated_at"] = datetime.now(UTC).isoformat()
            save_json(state_path, state)

        save_state(state.get("status", "started"))
        if story_file and not state.get("fixed_story"):
            raise ValueError("Cannot change an existing run to a different story")
        if review_only and not state.get("fixed_story"):
            raise ValueError("Review-only requires a supplied fixed story or its saved run")
        if state.get("fixed_story"):
            snapshot = job_dir / "settings-snapshot.json"
            if snapshot.exists():
                if json.loads(snapshot.read_text()) != self.settings.raw:
                    raise ValueError("Resume settings differ from this run's saved configuration")
            else:
                save_json(snapshot, self.settings.raw)
            saved_input = job_dir / "supplied-story.json"
            if saved_input.exists():
                if story_file and _load_model(story_file, StoryPackage) != _load_model(saved_input, StoryPackage):
                    raise ValueError("Supplied story differs from this run's immutable input")
            elif story_file:
                save_json(saved_input, _load_model(story_file, StoryPackage))
            else:
                raise ValueError("Fixed-story run is missing its original input; refusing story generation")
        service = None if dry_run else self.service_factory(self.settings, ledger)
        if human_approved:
            service.skip_story_review = True
        store = None if dry_run else ContentStore(self.settings.path("database"))
        previous = [] if dry_run else store.prior_ideas()  # type: ignore[union-attr]

        try:
            if not dry_run and not (job_dir / "model-access.json").exists():
                access = service.verify_model_access()  # type: ignore[union-attr]
                save_json(
                    job_dir / "model-access.json",
                    {
                        "checked_at": datetime.now(UTC).isoformat(),
                        "models": access,
                        "billing": "Model retrieval is a metadata check, not a generation request",
                    },
                )

            story_path = job_dir / "story.json"
            review_path = job_dir / "review.json"
            if human_approved:
                approval_path = job_dir / "human-approval.json"
                if not approval_path.exists():
                    raise ValueError("Saved final human approval is required")
                approval = json.loads(approval_path.read_text())
                story = _load_model(job_dir / "supplied-story.json", StoryPackage)
                digest = hashlib.sha256(story.model_dump_json().encode()).hexdigest()
                legacy_digest = hashlib.sha256(story.model_dump_json(exclude={"narration", "youtube_disclosure"}).encode()).hexdigest()
                legacy_input = "narration" not in json.loads((job_dir / "supplied-story.json").read_text())
                allowed_digests = {digest, legacy_digest} if legacy_input else {digest}
                if approval.get("story_sha256") not in allowed_digests or not approval.get("final_approval"):
                    raise ValueError("Final approval does not match the exact saved story")
                if story_path.exists() and _load_model(story_path, StoryPackage) != story:
                    raise ValueError("Saved story differs from the exact approved script")
                local_checks(story, self.settings, [], expected_format=format_name, advisory_editorial=True)
                save_json(story_path, story)
                self.emit("story_saved", "completed", "Your approval is final. Production will begin immediately.")
                save_state("human_approved", approval_mode="final_human")
            elif state.get("fixed_story"):
                story = _load_model(job_dir / "supplied-story.json", StoryPackage)
                if story_path.exists() and _load_model(story_path, StoryPackage) != story:
                    raise ValueError("Saved story differs from the exact supplied script")
                save_json(story_path, story)
                self.emit("story_saved", "completed", "Your approved story is saved")
                local_checks(story, self.settings, [], expected_format=format_name)
                ledger.add_local_stage("story_supplied_by_user", "Exact user script; no story-generation request allowed")
                if not review_path.exists():
                    if any(item.get("stage") == "quality_review" for item in ledger.requests):
                        raise RuntimeError("Review was already attempted; refusing a duplicate paid review")
                    self.emit("quality_review", "running", "Independent review; media waits for approval")
                    review = service.review_story(story)  # type: ignore[union-attr]
                    save_json(review_path, review)
                review = _load_model(review_path, StoryReview)
                # A rejection is terminal for this script, including on resume.
                review_checks(review, self.settings)
                self.emit("quality_review", "completed", "Independent review approved")
                save_state("reviewed")
                if review_only:
                    return review_path
            if human_approved:
                pass  # No review, rewrite or story-generation branch is reachable.
            elif story_path.exists() and review_path.exists():
                story = _load_model(story_path, StoryPackage)
                review = _load_model(review_path, StoryReview)
                current_idea = f"{story.title} {story.premise}"
                prior_for_resume = [idea for idea in previous if idea != current_idea]
                local_checks(
                    story,
                    self.settings,
                    prior_for_resume,
                    expected_format=format_name,
                    # Preserve previously accepted runs under their historical editorial rules.
                    enforce_editorial=review.category_scores is not None,
                )
                if dry_run:
                    if review.originality_risk != "low" or review.policy_risk != "low":
                        raise SafetyError("Dry-run review fixture has elevated risk")
                else:
                    review_checks(
                        review, self.settings,
                        enforce_editorial=review.category_scores is not None,
                    )
            else:
                errors: list[str] = []
                story = None
                review = None

                def evaluate_candidate(
                    candidate: StoryPackage,
                    candidate_review: StoryReview | None,
                    review_destination: Path | None,
                ) -> tuple[StoryPackage, StoryReview] | None:
                    try:
                        local_checks(
                            candidate,
                            self.settings,
                            previous,
                            expected_format=format_name,
                            enforce_editorial=not dry_run,
                        )
                        if candidate_review is None:
                            if dry_run:
                                candidate_review = demo_review()
                            else:
                                completed_reviews = sum(
                                    1
                                    for item in ledger.requests
                                    if item.get("stage") == "quality_review"
                                    and item.get("status") in {"succeeded", "failed"}
                                )
                                maximum_reviews = int(
                                    self.settings.safety["max_review_attempts"]
                                )
                                if completed_reviews >= maximum_reviews:
                                    raise SafetyError(
                                        f"quality-review request limit reached ({maximum_reviews})"
                                    )
                                candidate_review = service.review_story(candidate)  # type: ignore[union-attr]
                            if review_destination is not None:
                                save_json(review_destination, candidate_review)
                        if dry_run:
                            ledger.add_local_stage(
                                "quality_review_fixture",
                                "Offline-only fixture review; never accepted as a real quality review",
                            )
                        else:
                            review_checks(candidate_review, self.settings)
                        return candidate, candidate_review
                    except SafetyError as exc:
                        errors.append(str(exc))
                        previous.append(f"Rejected concept: {candidate.title} {candidate.premise}")
                        return None

                rejected_fixture_path = (
                    job_dir / "rejected-fixture-review.json"
                    if fixture == "mirror"
                    else job_dir / f"rejected-{fixture}-review.json"
                )
                if dry_run:
                    accepted = evaluate_candidate(demo_story(), demo_review(), None)
                    if accepted is not None:
                        story, review = accepted
                elif fixture in {
                    "mirror",
                    "matter-printer",
                    "matter-printer-final",
                    "eighth-shadow",
                    "eighth-shadow-final",
                } and not rejected_fixture_path.exists():
                    if fixture == "mirror":
                        candidate = demo_story()
                    elif fixture == "eighth-shadow":
                        candidate = eighth_shadow_story()
                    elif fixture == "eighth-shadow-final":
                        candidate = eighth_shadow_final_story()
                    else:
                        candidate = curated_story()
                    ledger.add_local_stage(
                        f"story_generation_fixture_{fixture.replace('-', '_')}",
                        f"Local {fixture} fixture selected; no story-generation API call",
                    )
                    accepted = evaluate_candidate(candidate, None, rejected_fixture_path)
                    if accepted is None:
                        # evaluate_candidate persists any returned review. Local gate failures
                        # have no review and never justify an API call.
                        if not rejected_fixture_path.exists():
                            save_json(rejected_fixture_path, {"local_rejection": errors[-1]})
                    else:
                        story, review = accepted

                maximum_generated = int(self.settings.safety["max_generation_attempts"])
                completed_generated = sum(
                    1
                    for item in ledger.requests
                    if item.get("stage") == "story_generation"
                    and item.get("status") in {"succeeded", "failed"}
                )
                for attempt_number in range(1, maximum_generated + 1):
                    if story is not None:
                        break
                    candidate_path = job_dir / f"story-attempt-{attempt_number:02d}.json"
                    candidate_review_path = (
                        job_dir / f"story-attempt-{attempt_number:02d}-review.json"
                    )
                    rejection_path = job_dir / f"story-attempt-{attempt_number:02d}-rejected.json"
                    if rejection_path.exists():
                        continue
                    if candidate_path.exists():
                        candidate = _load_model(candidate_path, StoryPackage)
                    elif attempt_number <= completed_generated:
                        errors.append(
                            f"generated attempt {attempt_number} completed before candidate "
                            "persistence and will not be repeated"
                        )
                        continue
                    else:
                        candidate = service.create_story(  # type: ignore[union-attr]
                            format_name, previous
                        )
                        save_json(candidate_path, candidate)
                        completed_generated += 1
                    candidate_review = (
                        _load_model(candidate_review_path, StoryReview)
                        if candidate_review_path.exists()
                        else None
                    )
                    accepted = evaluate_candidate(
                        candidate,
                        candidate_review,
                        candidate_review_path,
                    )
                    if accepted is None:
                        save_json(
                            rejection_path,
                            {
                                "rejected_at": datetime.now(UTC).isoformat(),
                                "reasons": errors[-1:],
                            },
                        )
                    else:
                        story, review = accepted
                if story is None or review is None:
                    raise RuntimeError("No story passed quality gates: " + " | ".join(errors))
                save_json(story_path, story)
                save_json(review_path, review)
                save_state("reviewed")

            if not dry_run and state.get("content_id") is None:
                content_id = store.create(story, job_dir)  # type: ignore[union-attr]
                save_state("human_approved" if human_approved else "reviewed", content_id=content_id)
            content_id = state.get("content_id")

            self.emit("image_prompts", "running", "Checking the eight saved scene prompts")
            prepared_prompts = [scene.visual_prompt for scene in story.scenes]
            plan = None
            if self.settings.raw.get("dashboard_brief") and not dry_run:
                plan_path = job_dir / "visual-plan.json"
                source_path = job_dir / "visual-plan-source.json"
                source = {"title": story.title, "scene_narration": [s.narration for s in story.scenes],
                          "style": self.settings.brand["visual_style"]}
                source_hash = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
                if plan_path.exists():
                    if not source_path.exists() or json.loads(source_path.read_text()).get("sha256") != source_hash:
                        raise ValueError("Saved visual plan does not match the approved story; no assets will be generated")
                    plan = _load_model(plan_path, VisualPlan)
                elif ledger.stage_succeeded("image_prompts"):
                    raise RuntimeError("Visual plan request succeeded but the saved plan is missing; refusing a repeat")
                else:
                    save_json(source_path, {"sha256": source_hash})
                    plan = service.prepare_image_prompts(story)  # type: ignore[union-attr]
                    save_json(plan_path, plan)
                prepared_prompts = [plan.continuity_bible + "\n\n" + prompt for prompt in plan.prompts]
                if enabled(self.settings):
                    prepared_prompts = [contract(self.settings) + "\n" + prompt for prompt in prepared_prompts]
                    for prompt in prepared_prompts:
                        require_content_only(prompt)
                    save_json(job_dir / "production-metadata.json", {**FLAGS, "description": youtube_description(story),
                              "youtube_disclosure": story.youtube_disclosure or DISCLOSURE,
                              "recurring_cast": self.settings.raw["recurring_cast"]})
                    save_json(job_dir / "image-prompts.json", {"prompts": prepared_prompts})
                    performance_path = job_dir / "narration-performance-plan.json"
                    performance = make_plan(story.narration, self.settings)
                    if performance_path.exists() and json.loads(performance_path.read_text()) != performance:
                        raise ValueError("Saved narration plan no longer matches this story")
                    if not performance_path.exists():
                        save_json(performance_path, performance)
            for prompt in prepared_prompts:
                require_content_only(prompt)
            self.emit("image_prompts", "completed", "Eight scene prompts ready")

            if dry_run:
                images = [job_dir / f"scene-{index:02d}.png" for index in range(1, 9)]
                if not all(_valid_image(path) for path in images):
                    images = create_demo_images(story, self.settings, job_dir)
                source_narration = job_dir / "narration-source.wav"
                narration = job_dir / "narration.wav"
                if not source_narration.exists():
                    create_local_narration(story.narration, source_narration)
                if not narration.exists():
                    normalize_narration(source_narration, narration, 55.0, 65.0)
            else:
                images = []
                for index, scene in enumerate(story.scenes, 1):
                    image_path = job_dir / f"scene-{index:02d}.png"
                    stage = f"image_{index:02d}"
                    self.emit(stage, "running", f"Image {index} of 8; saved files are reused")
                    if image_path.exists():
                        if not _valid_image(image_path):
                            raise ValueError(f"Saved asset is malformed and will not be regenerated: {image_path}")
                    elif ledger.stage_succeeded(stage):
                        raise RuntimeError(
                            f"{stage} was charged successfully but its file is missing; refusing regeneration"
                        )
                    else:
                        recompositions = 0
                        max_recompositions = 2
                        while True:
                            try:
                                service.create_image(  # type: ignore[union-attr]
                                    prepared_prompts[index - 1], image_path, story.format, index
                                )
                                break
                            except AmbiguousPaidRequest:
                                last = ledger.requests[-1]
                                can_recompose = (
                                    plan is not None
                                    and last.get("stage") == stage
                                    and last.get("charge_status") == "known_zero_charge_rejected"
                                    and recompositions < max_recompositions
                                )
                                if not can_recompose:
                                    raise
                                recompositions += 1
                                self.emit(
                                    stage, "running",
                                    f"Image {index} of 8; repeated rejection, trying a different composition ({recompositions}/{max_recompositions})",
                                )
                                new_scene_prompt = service.replan_scene_prompt(  # type: ignore[union-attr]
                                    story, index, plan.continuity_bible, prepared_prompts[index - 1]
                                )
                                prepared_prompts[index - 1] = plan.continuity_bible + "\n\n" + new_scene_prompt
                                save_json(job_dir / "image-prompts.json", {"prompts": prepared_prompts})
                    images.append(image_path)
                    save_state("assets_in_progress", completed_images=index)
                    self.emit(stage, "completed", f"Image {index} of 8 saved")

                source_narration = job_dir / "narration-source.wav"
                narration = job_dir / "narration.wav"
                self.emit("speech_generation", "running", "Preparing narration")
                spec = self.settings.formats[format_name]
                minimum, maximum = float(spec["duration_min_seconds"]), float(spec["duration_max_seconds"])
                max_pacing_retakes = 2
                pacing_retakes = 0
                pacing_retake_duration = 0.0
                while True:
                    if not source_narration.exists():
                        if ledger.stage_succeeded("speech_generation") and pacing_retakes == 0:
                            raise RuntimeError(
                                "Speech request succeeded but its file is missing; refusing regeneration"
                            )
                        pacing_hint = ""
                        if pacing_retakes > 0:
                            previous_duration = pacing_retake_duration
                            target = (minimum + maximum) / 2
                            pacing_hint = (
                                "The previous performance was too short; speak more slowly with fuller "
                                "natural pauses, do not rush." if previous_duration < target else
                                "The previous performance was too long; speak a little more quickly and "
                                "concisely, with minimal extra pauses."
                            )
                        service.create_speech(story.narration, source_narration, pacing_hint=pacing_hint)  # type: ignore[union-attr]
                        raw_duration = media_duration(source_narration)
                        service.finalize_speech_cost(raw_duration, story.narration)  # type: ignore[union-attr]
                    if narration.exists():
                        break
                    try:
                        normalize_narration(source_narration, narration, minimum, maximum)
                        break
                    except ValueError:
                        if pacing_retakes >= max_pacing_retakes:
                            raise
                        pacing_retake_duration = media_duration(source_narration)
                        pacing_retakes += 1
                        source_narration.replace(job_dir / f"narration-source-attempt-{pacing_retakes:02d}.wav")
                        self.emit(
                            "speech_generation", "running",
                            f"Narration was {pacing_retake_duration:.1f}s outside the {minimum:.0f}-{maximum:.0f}s "
                            f"range; requesting a re-take ({pacing_retakes}/{max_pacing_retakes})",
                        )
                save_state("assets_complete")
                self.emit("speech_generation", "completed", "Narration saved and duration checked")

            duration = media_duration(narration)
            alignment_path = job_dir / "alignment.json"
            self.emit("caption_alignment", "running", "Aligning words to the measured narration")
            if alignment_path.exists():
                alignment = _load_model(alignment_path, NarrationAlignment)
            elif dry_run:
                alignment = synthetic_alignment(story, duration)
                save_json(alignment_path, alignment)
            else:
                if ledger.stage_succeeded("caption_alignment"):
                    raise RuntimeError(
                        "Alignment request succeeded but its file is missing; refusing regeneration"
                    )
                alignment = service.align_speech(narration, duration)  # type: ignore[union-attr]
                save_json(alignment_path, alignment)
            save_state("aligned")
            self.emit("caption_alignment", "completed", "Word timing saved")

            video = render_video(story, images, narration, alignment, self.settings, job_dir,
                                 progress=self.emit, check_cancel=self.check_cancel)
            self.emit("validation", "running", "Checking the final video and audio")
            validation = validate_video(video, self.settings, story, alignment)
            save_json(job_dir / "validation.json", validation)
            self.emit("validation", "completed", "Technical validation passed")
            manifest = {
                "content_id": content_id,
                "story": str(story_path),
                "review": str(review_path),
                "images": [str(path) for path in images],
                "narration_source": str(source_narration),
                "narration": str(narration),
                "alignment": str(alignment_path),
                "captions": str(job_dir / "captions.srt"),
                "video": str(video),
                "cost_report": str(job_dir / "cost-report.json"),
                "dry_run": dry_run,
            }
            save_json(job_dir / "manifest.json", manifest)
            save_state("complete", video=str(video))
            self.emit("complete", "completed", "Your local Short is ready")
            if store is not None and content_id is not None:
                store.update(content_id, "rendered", video_path=str(video))
            return video
        except Exception as exc:
            save_state("failed", error=f"{type(exc).__name__}: {str(exc)[:1000]}")
            content_id = state.get("content_id")
            if store is not None and content_id is not None:
                store.update(content_id, "failed", error=str(exc)[:1000])
            raise
