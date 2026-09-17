from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Scene(StrictModel):
    narration: str = Field(min_length=10)
    visual_prompt: str = Field(min_length=20)
    on_screen_emphasis: str = Field(default="", max_length=80)


class CategoryScore(StrictModel):
    name: str = Field(min_length=2, max_length=60)
    score: int = Field(ge=1, le=10)


class StoryFields(StrictModel):
    format: Literal["short", "long"]
    title: str = Field(min_length=8, max_length=90)
    premise: str = Field(min_length=20, max_length=400)
    hook: str = Field(min_length=8, max_length=180)
    scenes: list[Scene] = Field(min_length=1, max_length=32)
    description: str = Field(min_length=20, max_length=1000)
    hashtags: list[str] = Field(min_length=2, max_length=5)
    continuity_bible: str = ""
    creative_fingerprint: dict[str, str] = Field(default_factory=dict)
    draft_scores: dict[str, int] = Field(default_factory=dict)
    story_category: str = ""
    draft_category_scores: list[CategoryScore] = Field(default_factory=list)
    # Only ever populated for real, factual Viral Material content that is genuinely about one
    # or more specific real, identifiable people (e.g. a real athlete's real documented career
    # story). Empty for ordinary fiction and for non-biographical real content (a recipe, a
    # science fact). Open-ended real names, unlike the fixed four-person recurring-cast roster.
    real_people: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        normalized = [v if v.startswith("#") else f"#{v}" for v in values]
        if any(not value[1:].replace("_", "").isalnum() for value in normalized):
            raise ValueError("hashtags may contain only letters, numbers, and underscores")
        return normalized

    @field_validator("real_people")
    @classmethod
    def normalize_real_people(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(v.split()) for v in values]
        return [v for v in cleaned if v]


class StoryPackage(StoryFields):
    # Saved application schema, distinct from the model's response schema.
    narration: str = Field(min_length=10)
    youtube_disclosure: str = ""

    @model_validator(mode="before")
    @classmethod
    def read_legacy_scenes(cls, data):
        if isinstance(data, dict) and "narration" not in data:
            data = dict(data)
            data["narration"] = " ".join(
                (s.narration if isinstance(s, Scene) else s.get("narration", "")).strip()
                for s in data.get("scenes", [])).strip()
        return data

    @property
    def word_count(self) -> int:
        return len(self.narration.split())


class EditorialScores(StrictModel):
    simple_language: int = Field(ge=1, le=10)
    opening_hook: int = Field(ge=1, le=10)
    coherence: int = Field(ge=1, le=10)
    category_match: int = Field(ge=1, le=10)
    ending_payoff: int = Field(ge=1, le=10)


class LegacyEditorialScores(StrictModel):
    """Read-only compatibility for old reviews; cannot approve new work."""
    simple_language: int = Field(ge=1, le=10)
    opening_hook: int = Field(ge=1, le=10)
    suspense_growth: int = Field(ge=1, le=10)
    emotional_stakes: int = Field(ge=1, le=10)
    final_twist: int = Field(ge=1, le=10)


class VisualPlan(StrictModel):
    continuity_bible: str = Field(min_length=40, max_length=4000)
    prompts: list[str] = Field(min_length=8, max_length=8)

    @field_validator("prompts")
    @classmethod
    def complete_prompts(cls, values):
        if any(not 30 <= len(value) <= 3000 for value in values):
            raise ValueError("Each scene needs a bounded, descriptive prompt")
        return values


class SceneRecomposition(StrictModel):
    prompt: str = Field(min_length=30, max_length=3000)


class CreativeFingerprint(StrictModel):
    main_object: str = Field(min_length=2, max_length=160)
    setting: str = Field(min_length=2, max_length=160)
    characters: str = Field(min_length=2, max_length=160)
    twist: str = Field(min_length=2, max_length=240)


class StoryDraftResponse(StoryFields):
    """Closed API schema; historical StoryPackage dictionaries remain readable locally."""
    narration: str = Field(min_length=10, description="Complete spoken story only. No disclosure, disclaimer or metadata.")
    creative_fingerprint: CreativeFingerprint
    draft_scores: EditorialScores
    story_category: str = Field(min_length=3, max_length=60)
    draft_category_scores: list[CategoryScore] = Field(min_length=1, max_length=5)


class PublicFigureStoryResponse(StoryDraftResponse):
    # A story features one or two of the permanent four-person roster, never all four and never zero.
    named_characters: list[Literal["Lionel Messi", "Cristiano Ronaldo", "IShowSpeed", "MrBeast"]] = Field(min_length=1, max_length=2)
    speaking_characters: list[Literal["Lionel Messi", "Cristiano Ronaldo", "IShowSpeed", "MrBeast"]] = Field(max_length=2)

    @field_validator("named_characters")
    @classmethod
    def one_or_two_unique_roster_members(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Each chosen recurring character may be named only once")
        return values

    @field_validator("speaking_characters")
    @classmethod
    def speaking_characters_are_unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Each speaking character may appear only once")
        return values


class StoryReview(StrictModel):
    score: int = Field(ge=1, le=10)
    approved: bool
    originality_risk: Literal["low", "medium", "high"]
    policy_risk: Literal["low", "medium", "high"]
    problems: list[str] = Field(default_factory=list)
    correction_notes: str = ""
    # Null permits reading historical reviews, but cannot approve a new story.
    category_scores: EditorialScores | LegacyEditorialScores | None = None
    reviewed_category: str = ""
    category_specific_scores: list[CategoryScore] = Field(default_factory=list)
    editorial_problems: list[str] = Field(default_factory=list)
    evidence: str = ""


class StoryReviewResponse(StoryReview):
    """New API responses must use the new rubric, never the legacy alternative."""
    category_scores: EditorialScores
    reviewed_category: str = Field(min_length=3, max_length=60)
    category_specific_scores: list[CategoryScore] = Field(min_length=1, max_length=5)


class RenderManifest(StrictModel):
    story: StoryPackage
    image_files: list[str]
    narration_file: str
    duration_seconds: float
    video_file: str = ""


class TimedWord(StrictModel):
    word: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @field_validator("end")
    @classmethod
    def end_must_follow_start(cls, value: float, info) -> float:
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("word end must be after word start")
        return value


class NarrationAlignment(StrictModel):
    model: str
    transcript: str = Field(min_length=10)
    duration_seconds: float = Field(gt=0)
    words: list[TimedWord] = Field(min_length=1)
