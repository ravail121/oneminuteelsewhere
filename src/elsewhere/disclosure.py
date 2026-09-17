"""Backend-owned YouTube metadata. Never a spoken or visual production input."""
import re

DISCLOSURE = (
    "This is an unofficial, fictional AI-generated story created for entertainment. "
    "It is not connected to or endorsed by Lionel Messi, Cristiano Ronaldo, IShowSpeed, MrBeast, "
    "their clubs, channels, sponsors, or representatives. "
    "This video uses AI-generated visuals and narration."
)

# Permanent four-person roster. Any ONE story features one or two of them (recurring_cast.RULES),
# never all four — so titles/tags/hashtags below are computed per-story from whichever are actually
# present in that story's own narration, never a fixed "always all four" assumption.
ROSTER = ("Messi", "Ronaldo", "IShowSpeed", "MrBeast")
_ROSTER_FULL_NAMES = {"Messi": "Lionel Messi", "Ronaldo": "Cristiano Ronaldo",
                      "IShowSpeed": "IShowSpeed", "MrBeast": "MrBeast"}
# Either the first name alone or the surname/stage-name alone counts as that person being
# present (matches how dialogue naturally attributes lines, e.g. "Lionel asked for paint").
_ROSTER_PATTERNS = {"Messi": r"Lionel|Messi", "Ronaldo": r"Cristiano|Ronaldo",
                    "IShowSpeed": r"IShowSpeed", "MrBeast": r"Mr\.?\s*Beast"}
_ALL_ROSTER_MARKERS = ("messi", "ronaldo", "ishowspeed", "beast")  # for the fixed DISCLOSURE text only

_CAST_NAME = re.compile("|".join(rf"\b(?:{pattern})\b" for pattern in _ROSTER_PATTERNS.values()), re.IGNORECASE)


def detected_cast(text: str) -> list[str]:
    """Roster members actually named in text (e.g. a story's narration), in permanent roster
    order, deduplicated. The one honest source of truth for "who is really in this story" —
    never a fixed assumption, since a story features only one or two of the four."""
    text = text or ""
    return [name for name in ROSTER if re.search(rf"\b(?:{_ROSTER_PATTERNS[name]})\b", text, re.IGNORECASE)]


# Permanent title contract: the story's own one or two cast names, once each, at the very
# start, ending in #Shorts.
TITLE_SUFFIX = " #Shorts"
TITLE_SOFT_LIMIT = 70
TITLE_HARD_LIMIT = 100  # YouTube's actual video-title character limit.
TITLE_BANNED_WORDS = ("official", "real", "leaked", "caught")
TITLE_BANNED_PHRASES = ("true story", "real footage", "actually happened", "really happened")

MAX_TAGS_CHARS = 500  # YouTube's combined-tags character budget.

# One generated tag per selected story category, layered on the permanent base tags.
CATEGORY_TAGS = {
    "Suspense": ("suspense story",),
    "Thriller": ("thriller story",),
    "Mystery": ("mystery story",),
    "Horror": ("horror story",),
    "Science Fiction": ("science fiction story",),
    "Crime": ("crime story", "mystery story"),
    "Funny": ("funny story",),
    "Emotional": ("emotional story",),
    "Inspirational": ("inspirational story",),
    "Dark Twist": ("dark twist story", "mystery story"),
    "Mystery with Final Twist": ("mystery story", "twist story"),
}

# Tags and hashtags for a roster member are only ever added when that person is ACTUALLY in
# the story (per detected_cast), never blanket-applied to every video — adding "MrBeast" tags
# to a video that doesn't feature him would be misleading tag-stuffing, the same reason real
# club/sponsor names are excluded below.
PERSON_TAGS = {
    "Messi": ("Messi", "Lionel Messi", "Messi story"),
    "Ronaldo": ("Ronaldo", "Cristiano Ronaldo", "Ronaldo story"),
    "IShowSpeed": ("IShowSpeed", "IShowSpeed story"),
    "MrBeast": ("MrBeast", "MrBeast story"),
}
# Researched 2026-09-14/16: #Shorts is YouTube's own top Shorts-discovery hashtag; each roster
# member's own name/nickname hashtags are heavily searched. Deliberately excludes real club,
# competition and sponsor names (Real Madrid, Barcelona, PSG, Champions League, FIFA, UEFA,
# Nike, Adidas, ...), any other real athlete's name (Neymar, ...), and real MrBeast/IShowSpeed
# product or merch tags (Feastables, MrBeast Burger, Beast Games, shop/merch tags, ...): those
# would break the "no real club/sponsor branding, no endorsements, no other named public
# figure" contract in recurring_cast.py and could read as implying a real affiliation or event.
PERSON_HASHTAGS = {
    "Messi": ("#Messi", "#LeoMessi"),
    "Ronaldo": ("#Ronaldo", "#CristianoRonaldo", "#CR7"),
    "IShowSpeed": ("#IShowSpeed",),
    "MrBeast": ("#MrBeast",),
}
# Only relevant, and only added, when Messi or Ronaldo is actually in the story.
FOOTBALL_HASHTAGS = ("#Football", "#Soccer", "#GOAT")
MAX_DESCRIPTION_HASHTAGS = 12  # well under YouTube's 60-hashtag cutoff where it ignores all of them


def strip_disclosure(text: str) -> str:
    # Only the fixed metadata sentences, not fictional story content, are removed.
    for sentence in re.findall(r"[^.]+\.", DISCLOSURE):
        pattern = r"\s+".join(re.escape(word) for word in sentence.strip().split())
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def require_content_only(text: str) -> None:
    if strip_disclosure(text) != text.strip():
        raise ValueError("YouTube disclosure metadata cannot enter narration or media inputs")


def require_story_content(story) -> None:
    require_content_only(story.narration)
    for text in [story.title, story.hook, story.continuity_bible,
                 *(s.narration for s in story.scenes), *(s.visual_prompt for s in story.scenes),
                 *(s.on_screen_emphasis for s in story.scenes)]:
        require_content_only(text)
    if story.narration.split() != " ".join(s.narration for s in story.scenes).split():
        raise ValueError("Scene narration must partition the exact narration field, in order, without extra text")


def youtube_description(story) -> str:
    return "\n\n".join(s for s in (strip_disclosure(story.description), story.youtube_disclosure) if s)


def is_recurring_cast_story(story) -> bool:
    """True only for stories the backend actually stamped with the recurring-cast disclosure.

    Checks for all four roster markers rather than exact equality to the current DISCLOSURE
    text, so a story stamped under an earlier wording of the same backend-owned disclosure is
    still recognized; only ever set by recurring_cast.with_disclosure, never free story content.
    The disclosure itself always names the full roster regardless of which one or two a given
    story actually features (a safe, always-accurate umbrella disclaimer either way).
    """
    text = (story.youtube_disclosure or "").lower()
    return bool(text) and all(marker in text for marker in _ALL_ROSTER_MARKERS)


def _clean_hook(text: str) -> str:
    hook = _CAST_NAME.sub("", text or "")
    return re.sub(r"\s+", " ", hook).strip(" -:,.")


def _title_prefix(names: list[str]) -> str:
    if len(names) == 1:
        return f"{names[0]} "
    return f"{' and '.join(names[:-1])} and {names[-1]} "


def _canonical_name(matched_text: str) -> str:
    # These roster substrings never overlap, so a plain substring check is enough to classify
    # which roster member a regex match refers to (first name alone or surname/stage name alone).
    lowered = matched_text.lower()
    if "messi" in lowered or "lionel" in lowered:
        return "Messi"
    if "ronaldo" in lowered or "cristiano" in lowered:
        return "Ronaldo"
    if "ishowspeed" in lowered:
        return "IShowSpeed"
    return "MrBeast"


def validate_youtube_title(title: str, expected_names: list[str] | None = None) -> None:
    """Reject a title that breaks a permanent, non-negotiable publishing rule.

    expected_names, when given, pins the exact one or two names this specific title must use
    (e.g. when regenerating/checking a known story). Otherwise any well-formed one-or-two-name
    title is accepted structurally, since which names are correct varies story to story.
    """
    matches = list(_CAST_NAME.finditer(title))
    if not matches:
        raise ValueError("The YouTube title must name at least one recurring cast member")
    if len(matches) > 2:
        raise ValueError("The YouTube title may feature at most two recurring cast members")
    found = [_canonical_name(match.group(0)) for match in matches]
    if len(set(found)) != len(found):
        raise ValueError("The YouTube title must mention each cast member exactly once")
    if expected_names is not None and found != list(expected_names):
        raise ValueError("The YouTube title must name exactly this story's own cast members")
    if matches[0].start() > 0 or matches[-1].end() > 45:
        raise ValueError("Cast names must appear together near the very beginning of the YouTube title")
    lowered_title = title.lower()
    if any(word in lowered_title for word in TITLE_BANNED_WORDS) or any(phrase in lowered_title for phrase in TITLE_BANNED_PHRASES):
        raise ValueError("The YouTube title cannot use 'official', 'real', 'leaked', 'caught' or claim a true event")
    if not title.endswith("#Shorts"):
        raise ValueError("The YouTube title must end with #Shorts")
    if len(title) > TITLE_HARD_LIMIT:
        raise ValueError("The YouTube title exceeds YouTube's title length limit")


def youtube_title(story, hook_override: str | None = None) -> str:
    """Deterministic title naming exactly this story's own one or two cast members."""
    names = detected_cast(story.narration)[:2] or ["Messi", "Ronaldo"]
    prefix = _title_prefix(names)
    hook = _clean_hook(hook_override) if hook_override else ""
    hook = hook or _clean_hook(story.title) or "A New Story"
    budget = TITLE_SOFT_LIMIT - len(prefix) - len(TITLE_SUFFIX)
    if budget > 0 and len(hook) > budget:
        hook = hook[:budget].rsplit(" ", 1)[0] or hook[:budget]
    title = f"{prefix}{hook}{TITLE_SUFFIX}"
    if len(title) > TITLE_HARD_LIMIT:
        overflow = len(title) - TITLE_HARD_LIMIT
        hook = hook[: max(1, len(hook) - overflow)].rsplit(" ", 1)[0] or hook[: max(1, len(hook) - overflow)]
        title = f"{prefix}{hook}{TITLE_SUFFIX}"
    validate_youtube_title(title, expected_names=names)
    return title


def validate_extra_tags(tags) -> None:
    for tag in tags:
        lowered = str(tag).lower()
        if any(word in lowered for word in TITLE_BANNED_WORDS):
            raise ValueError(f"Tag {tag!r} is not allowed")


def youtube_tags(settings, story, extra=None) -> list[str]:
    """This story's own cast tags, permanent generic base tags, and one category tag —
    deduplicated and length-capped."""
    if extra:
        validate_extra_tags(extra)
    category = (getattr(story, "story_category", "") or "").strip()
    present = detected_cast(getattr(story, "narration", "") or "")
    person_tags = [tag for name in present for tag in PERSON_TAGS.get(name, ())]
    if len(present) == 2:
        person_tags.append(f"{present[0]} and {present[1]}")
    candidates = [*person_tags, *settings.publishing.get("tags", []), *CATEGORY_TAGS.get(category, ()), *(extra or [])]
    seen, tags = set(), []
    for tag in candidates:
        cleaned = " ".join(str(tag).split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        tags.append(cleaned)
    kept, total = [], 0
    for tag in tags:
        addition = len(tag) + (1 if kept else 0)
        if total + addition > MAX_TAGS_CHARS:
            break
        kept.append(tag)
        total += addition
    return kept


def real_person_disclosure(people: list[str]) -> str:
    """Backend-built disclosure for factual Viral Material content that genuinely names one or
    more specific real, identifiable people. Unlike the fixed roster DISCLOSURE, the named
    person(s) vary story to story — this is generated from the story's own real_people field,
    never a story-content input itself."""
    if not people:
        return ""
    names = people[0] if len(people) == 1 else ", ".join(people[:-1]) + f" and {people[-1]}"
    return (f"This video retells publicly available information about {names}. It is not verified "
            f"in real time and is not officially affiliated with, endorsed by, or produced with "
            f"{names}. Some details may be simplified or summarized for a short video. "
            "This video uses AI-generated narration and visuals.")


def with_real_content_disclosure(story):
    """Attach the backend-built real-person disclosure for factual Viral Material content.
    A no-op when the story does not name any specific real people (a recipe, a science fact)."""
    people = getattr(story, "real_people", None) or []
    if not people:
        return story
    text = real_person_disclosure(people)
    return story if story.youtube_disclosure == text else story.model_copy(update={"youtube_disclosure": text})


def _dedupe_capped(items, *, limit=None, char_budget=None):
    seen, result, total = set(), [], 0
    for item in items:
        cleaned = " ".join(str(item).split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        if limit is not None and len(result) >= limit:
            break
        if char_budget is not None:
            addition = len(cleaned) + (1 if result else 0)
            if total + addition > char_budget:
                break
            total += addition
        seen.add(key)
        result.append(cleaned)
    return result


# Real Viral Material content is not fiction, so it never claims "fictional story"/"animated story".
VIRAL_BASE_TAGS = ("AI video", "short video", "YouTube Shorts", "One Minute Elsewhere", "viral")


def viral_tags(settings, story, extra=None) -> list[str]:
    """Base tags for factual Viral Material content: this story's own real-content category and
    any real people it names, plus generic (non-fiction-claiming) base tags."""
    if extra:
        validate_extra_tags(extra)
    category = (getattr(story, "story_category", "") or "").strip()
    people = list(getattr(story, "real_people", None) or [])
    candidates = [*people, *VIRAL_BASE_TAGS, *([category] if category else []), *(extra or [])]
    return _dedupe_capped(candidates, char_budget=MAX_TAGS_CHARS)


def viral_hashtags(story, *, limit=MAX_DESCRIPTION_HASHTAGS) -> list[str]:
    """This story's own hashtags plus #Shorts and simple real-people hashtags, for factual
    Viral Material content (no fixed roster involved)."""
    people = list(getattr(story, "real_people", None) or [])
    person_hashtags = [f"#{cleaned}" for name in people if (cleaned := re.sub(r"[^A-Za-z0-9]", "", name))]
    candidates = ["#Shorts", *person_hashtags, *story.hashtags, "#Viral"]
    return _dedupe_capped(candidates, limit=limit)


def combined_hashtags(story_hashtags, *, present=(), limit=MAX_DESCRIPTION_HASHTAGS) -> list[str]:
    """The story's own hashtags plus hashtags for exactly this story's own cast members.

    #Shorts and the story's actual cast go first so they always survive the cap and are the
    hashtags YouTube is most likely to show as pills above the title. present is the list from
    detected_cast(story.narration); football/soccer/GOAT hashtags only apply when Messi or
    Ronaldo is actually in the story, never added just because the roster includes footballers.
    """
    person_hashtags = [tag for name in present for tag in PERSON_HASHTAGS.get(name, ())]
    extra_viral = FOOTBALL_HASHTAGS if ({"Messi", "Ronaldo"} & set(present)) else ()
    seen, result = set(), []
    for tag in ("#Shorts", *person_hashtags, *story_hashtags, *extra_viral, "#Viral"):
        key = tag.lower()
        if key in seen or len(result) >= limit:
            continue
        seen.add(key)
        result.append(tag)
    return result
