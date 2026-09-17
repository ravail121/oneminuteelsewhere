from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from PIL import Image

from .api_diagnostics import validate_image_request
from .config import Settings
from .costs import (
    CostLedger,
    image_cost,
    speech_estimated_cost,
    text_cost,
    transcription_cost,
)
from .disclosure import require_content_only, require_story_content
from .models import (
    NarrationAlignment,
    PublicFigureStoryResponse,
    SceneRecomposition,
    StoryDraftResponse,
    StoryPackage,
    StoryReview,
    StoryReviewResponse,
    TimedWord,
    VisualPlan,
)
from .performance import instructions as performance_instructions
from .performance import make_plan
from .prompts import review_prompt, story_prompt, system_prompt
from .recurring_cast import VISUAL_IDENTITIES, contract, enabled, with_disclosure
from .rubrics import selected_category


class MissingAPIKey(RuntimeError):
    pass


def _usage(response):
    return getattr(response, "usage", None)


def _reserve(settings: Settings, stage: str) -> float:
    return float(settings.costs["request_reserves_usd"][stage])


class AIService:
    def __init__(self, settings: Settings, ledger: CostLedger):
        if not os.environ.get("OPENAI_API_KEY"):
            raise MissingAPIKey("OPENAI_API_KEY is not set in the local environment")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies first") from exc
        self.client = OpenAI(
            max_retries=int(settings.safety.get("max_sdk_retries", 0)),
            timeout=120.0,
        )
        self.settings = settings
        self.ledger = ledger

    def verify_model_access(self) -> list[dict[str, str | None]]:
        model_ids = {
            self.settings.models[name]
            for name in ("story", "reviewer", "image", "speech", "alignment")
            if name != "reviewer" or not getattr(self, "skip_story_review", False)
        }
        verified = []
        for model_id in sorted(model_ids):
            try:
                response = self.client.models.retrieve(model_id)
            except Exception as exc:
                raise RuntimeError(
                    f"The configured model {model_id!r} could not be retrieved for this API key"
                ) from exc
            verified.append(
                {
                    "requested": model_id,
                    "returned": str(getattr(response, "id", model_id)),
                    "request_id": str(getattr(response, "_request_id", "")) or None,
                }
            )
        return verified

    def create_story(self, format_name: str, prior_ideas: list[str]) -> StoryPackage:
        response = self.ledger.paid_call(
            stage="story_generation",
            model=self.settings.models["story"],
            reserve_usd=_reserve(self.settings, "story"),
            call=lambda: self.client.responses.parse(
                model=self.settings.models["story"],
                input=[
                    {"role": "system", "content": system_prompt(self.settings)},
                    {
                        "role": "user",
                        "content": story_prompt(self.settings, format_name, prior_ideas),
                    },
                ],
                text_format=PublicFigureStoryResponse if enabled(self.settings) else StoryDraftResponse,
                max_output_tokens=6000,
            ),
            usage_getter=_usage,
            cost_calculator=text_cost,
        )
        if response.output_parsed is None:
            raise RuntimeError("Story request succeeded but returned no valid structured package")
        payload = response.output_parsed.model_dump()
        if enabled(self.settings):
            parsed = PublicFigureStoryResponse.model_validate(payload)
            save_json(self.ledger.path.parent / "cast-manifest.json", {
                "named_characters": parsed.named_characters, "speaking_characters": parsed.speaking_characters})
            payload.pop("named_characters")
            payload.pop("speaking_characters")
        story = StoryPackage.model_validate(payload)
        return with_disclosure(story) if enabled(self.settings) else story

    def review_story(self, story: StoryPackage) -> StoryReview:
        if self.settings.raw.get("dashboard_brief") or getattr(self, "skip_story_review", False):
            raise RuntimeError("Paid story review is disabled: human approval is final")
        response = self.ledger.paid_call(
            stage="quality_review",
            model=self.settings.models["reviewer"],
            reserve_usd=_reserve(self.settings, "review"),
            call=lambda: self.client.responses.parse(
                model=self.settings.models["reviewer"],
                input=review_prompt(self.settings, story.model_dump_json(indent=2)),
                text_format=StoryReviewResponse,
                max_output_tokens=4096,
            ),
            usage_getter=_usage,
            cost_calculator=text_cost,
        )
        if response.output_parsed is None:
            raise RuntimeError("Review request succeeded but returned no valid structured review")
        return StoryReview.model_validate(response.output_parsed.model_dump())

    def create_image(self, prompt: str, destination: Path, format_name: str, index: int) -> None:
        require_content_only(prompt)
        spec = self.settings.formats[format_name]
        size = "1024x1536" if int(spec["height"]) > int(spec["width"]) else "1536x1024"
        if enabled(self.settings) and VISUAL_IDENTITIES not in prompt:
            prompt = contract(self.settings) + "\n" + prompt
        people_restriction = "other public figures" if enabled(self.settings) else "famous people"
        full_prompt = (
            f"{prompt}\n\nThis is frame {index} of the same continuous story. Preserve every repeated "
            f"character and location detail exactly. Consistent art direction: "
            f"{self.settings.brand['visual_style']}. No captions, letters, logos, interface "
            f"elements, {people_restriction}, recognizable copyrighted characters, or watermark."
        )
        require_content_only(full_prompt)
        validate_image_request(self.settings.models["image"], size, self.settings.models["image_quality"], "png", full_prompt)
        response = self.ledger.paid_call(
            stage=f"image_{index:02d}",
            model=self.settings.models["image"],
            reserve_usd=_reserve(self.settings, "image"),
            call=lambda: self.client.images.generate(
                model=self.settings.models["image"],
                prompt=full_prompt,
                size=size,
                quality=self.settings.models["image_quality"],
                output_format="png",
                moderation="low" if enabled(self.settings) else "auto",
            ),
            usage_getter=_usage,
            cost_calculator=image_cost,
        )
        encoded = response.data[0].b64_json if response.data else None
        if not encoded:
            raise RuntimeError("Image request succeeded but returned no image bytes")
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(base64.b64decode(encoded, validate=True))
        try:
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                if image.width < 1000 or image.height < 1500:
                    raise ValueError(f"Unexpected generated image dimensions: {image.size}")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(destination)

    def prepare_image_prompts(self, story: StoryPackage) -> VisualPlan:
        require_story_content(story)
        response = self.ledger.paid_call(
            stage="image_prompts", model=self.settings.models["story"],
            reserve_usd=_reserve(self.settings, "story"),
            call=lambda: self.client.responses.parse(
                model=self.settings.models["story"], text_format=VisualPlan, max_output_tokens=6000,
                input=("Prepare exactly eight vertical scene image prompts for this already approved story. "
                       "Do NOT rewrite or extend the narration. Give a complete shared continuity_bible "
                       "with the specified cast, faces, clothing, location and prop details. "
                       "Derive these details from the current narration, not stale draft visual prompts. "
                       "Make each scene visually distinct with clear action; preserve all identities and props. "
                       "Never show later events before the corresponding narration scene. "
                       "Follow the selected category and its ending, without adding suspense or a twist. "
                       "The FIRST scene image (prompts[0]) will likely be used as this video's thumbnail — the "
                       "single most important image for getting someone to stop scrolling and click. Make it "
                       "exceptionally eye-catching and clickbait-worthy: a striking pose or expression, bold "
                       "contrast, dramatic framing, or the story's most visually surprising beat, while staying "
                       "true to the story, continuity and identities. "
                       "The last scene must show the story's own payoff, without unintended duplicate characters "
                       "or props, impossible extra limbs, logos, requested text, unauthorized extra characters, "
                       "or copyrighted characters. No real crime depictions. Style: "
                       + str(self.settings.brand["visual_style"]) + "\n" + contract(self.settings) + "\n"
                       + json.dumps({"title": story.title, "selected_category": selected_category(self.settings),
                                     "scene_narration": [s.narration for s in story.scenes]})),
            ), usage_getter=_usage, cost_calculator=text_cost,
        )
        if response.output_parsed is None:
            raise RuntimeError("Visual planning returned no valid structured plan; it will not be repeated")
        plan = VisualPlan.model_validate(response.output_parsed.model_dump())
        for text in [plan.continuity_bible, *plan.prompts]:
            require_content_only(text)
        return plan

    def replan_scene_prompt(self, story: StoryPackage, index: int, continuity_bible: str, rejected_prompt: str) -> str:
        require_content_only(rejected_prompt)
        response = self.ledger.paid_call(
            stage=f"scene_replan_{index:02d}", model=self.settings.models["story"],
            reserve_usd=_reserve(self.settings, "story"),
            call=lambda: self.client.responses.parse(
                model=self.settings.models["story"], text_format=SceneRecomposition, max_output_tokens=1200,
                input=("The previous image prompt for this one scene was rejected by the image provider's "
                       "content-safety system. Do NOT repeat or lightly reword it. Propose a genuinely "
                       "different visual staging for the SAME narrative beat: a different camera angle, "
                       "arrangement or moment, while keeping the exact same characters, identities, "
                       "continuity details and narrative meaning. Avoid ambiguous poses: nothing held or "
                       "concealed behind a body, no obscured hands, no crowding. Keep it simple and "
                       "unambiguous to render safely. Never add suspense, danger, or content beyond the "
                       "existing story. Style and continuity:\n" + continuity_bible + "\n"
                       + "Scene narration for this beat: " + story.scenes[index - 1].narration + "\n"
                       + "Rejected prompt to avoid repeating: " + rejected_prompt),
            ), usage_getter=_usage, cost_calculator=text_cost,
        )
        if response.output_parsed is None:
            raise RuntimeError("Scene re-composition returned no valid structured result")
        plan = SceneRecomposition.model_validate(response.output_parsed.model_dump())
        require_content_only(plan.prompt)
        return plan.prompt

    def create_speech(self, text: str, destination: Path, *, pacing_hint: str = "") -> None:
        require_content_only(text)
        directions = self.settings.models["voice_instructions"]
        if enabled(self.settings) or self.settings.raw.get("dashboard_brief", {}).get("video_mode") == "viral":
            plan_path = destination.parent / "narration-performance-plan.json"
            expected = make_plan(text, self.settings)
            if plan_path.exists():
                plan = json.loads(plan_path.read_text())
                if plan != expected:
                    raise ValueError("Saved narration performance plan differs from the approved text or voice")
            else:
                plan = expected
                save_json(plan_path, plan)
            directions = performance_instructions(plan, directions)
            save_json(destination.parent / "narration-instructions.json", {"instructions": directions, "voice": plan["voice"]})
        if pacing_hint:
            directions = directions + " " + pacing_hint
        if len(directions) + len(text) > 5500:
            raise ValueError("Single-request narration instructions exceed the conservative local input bound")
        self._speech_instruction_text = directions
        require_content_only(directions)
        response = self.ledger.paid_call(
            stage="speech_generation",
            model=self.settings.models["speech"],
            reserve_usd=_reserve(self.settings, "speech"),
            call=lambda: self.client.audio.speech.with_raw_response.create(
                model=self.settings.models["speech"],
                voice=self.settings.models["voice"],
                input=text,
                instructions=directions,
                response_format="wav",
            ),
            usage_getter=lambda _: None,
            cost_calculator=lambda _: (
                _reserve(self.settings, "speech"),
                "Conservative reserve pending local duration measurement; API returned no usage",
            ),
        )
        temporary = destination.with_suffix(destination.suffix + ".part")
        parsed = response.parse()
        parsed.write_to_file(temporary)
        temporary.replace(destination)

    def finalize_speech_cost(self, duration_seconds: float, text: str) -> None:
        estimated_tokens = max(1, round(len((text + " " + getattr(self, "_speech_instruction_text", "")).split()) * 1.4))
        cost, basis = speech_estimated_cost(duration_seconds, estimated_tokens)
        self.ledger.revise_latest(
            "speech_generation",
            calculated_cost_usd=cost,
            cost_basis=basis,
            charge_status="unknown_usage_estimated_not_billing_confirmation",
            usage={
                "returned_by_api": None,
                "measured_output_duration_seconds": duration_seconds,
                "estimated_input_text_tokens": estimated_tokens,
            },
        )

    def align_speech(self, narration: Path, duration_seconds: float) -> NarrationAlignment:
        with narration.open("rb") as audio:
            response = self.ledger.paid_call(
                stage="caption_alignment",
                model=self.settings.models["alignment"],
                reserve_usd=_reserve(self.settings, "alignment"),
                call=lambda: self.client.audio.transcriptions.create(
                    file=audio,
                    model=self.settings.models["alignment"],
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                    language=self.settings.brand.get("language", "en"),
                ),
                usage_getter=_usage,
                cost_calculator=lambda usage: transcription_cost(
                    float(usage.get("seconds", duration_seconds)) if isinstance(usage, dict)
                    else float(getattr(usage, "seconds", duration_seconds))
                ),
            )
        # Persist the paid result before local timing validation, so recovery never needs
        # another transcription request. Contains transcript/usage, never request headers.
        save_json(self.ledger.path.parent / "caption-alignment-response.json", response)
        words = []
        for item in response.words or []:
            start = max(0.0, float(item.start))
            end = min(duration_seconds, float(item.end))
            if end <= start:
                end = min(duration_seconds, start + 0.02)
            if end <= start:
                start = max(0.0, duration_seconds - 0.02)
                end = duration_seconds
            words.append(TimedWord(word=item.word, start=start, end=end))
        return NarrationAlignment(
            model=self.settings.models["alignment"],
            transcript=response.text,
            duration_seconds=duration_seconds,
            words=words,
        )


def save_json(path: Path, value: object) -> None:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
