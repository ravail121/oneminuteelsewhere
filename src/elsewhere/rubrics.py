"""One authoritative category contract for prompts, review gates and browser labels."""
from __future__ import annotations

import re

DEFAULT_CATEGORY = "Mystery with Final Twist"
RUBRIC_VERSION = 3
GLOBAL_SCORES = {
    "simple_language": "Simple language", "opening_hook": "Opening hook",
    "coherence": "Coherence", "category_match": "Category match", "ending_payoff": "Ending payoff",
}
# Rules and score names are application-owned, never taken from model output.
RUBRICS = {
    "Suspense": (
        "Uncertainty from the opening; tension grows throughout; delay important information; strong suspenseful payoff.",
        {"tension": "Tension", "escalation": "Escalation"}),
    "Thriller": (
        "Immediate risk or danger; fast pace; escalating problems; an exciting climax.",
        {"danger": "Danger", "pace": "Pace", "escalation": "Escalation"}),
    "Mystery": (
        "One clear mystery; understandable clues that connect logically; satisfying explanation or reveal.",
        {"clue_quality": "Clue quality", "reveal": "Reveal"}),
    "Horror": (
        "Disturbing atmosphere; growing fear; one understandable threat; frightening ending; avoid excessive gore.",
        {"fear": "Fear", "atmosphere": "Atmosphere"}),
    "Science Fiction": (
        "One simple speculative idea; clear human impact; minimal technical vocabulary; understandable ending.",
        {"speculative_idea": "Speculative idea", "human_impact": "Human impact"}),
    "Crime": (
        ("Clear intentional but harmless fictional wrongdoing: a playful theft, trick or secret plan. "
         "A missing object alone is insufficient. Include useful connected clues and a clear solution. "
         "Do not resolve everything as an accident or innocent mistake. Avoid serious crimes and real-world allegations; obvious fiction only."),
        {"investigation": "Crime / investigation", "clue_quality": "Clue quality", "consequence": "Consequence"}),
    "Funny": (
        ("A genuinely funny situation; connected comedic escalation; simple understandable events; strong punchline or funny ending. "
         "Personal danger and suspense are not required."),
        {"comedy_strength": "Comedy strength", "punchline": "Punchline"}),
    "Emotional": (
        "Relatable characters or relationship; clear emotional problem; feelings grow naturally; moving and satisfying ending. A twist is optional.",
        {"emotional_impact": "Emotional impact", "relationship": "Relatable relationship"}),
    "Inspirational": (
        "Clear struggle; meaningful effort or decision; positive change; hopeful payoff; avoid sounding like a lecture. A dark twist is not required.",
        {"meaningful_effort": "Meaningful effort", "hopeful_payoff": "Hopeful payoff"}),
    "Dark Twist": (
        "Ordinary or interesting opening; growing unease; hidden truth; disturbing but understandable final reveal.",
        {"unease": "Unease", "final_reveal": "Final reveal"}),
    "Mystery with Final Twist": (
        ("Immediate mystery; connected clues; growing suspense; withhold explanation until the final 20 percent; "
         "powerful final twist in the last 5 to 8 seconds that changes how the opening is understood."),
        {"clue_quality": "Clue quality", "tension": "Tension", "final_twist": "Final twist"}),
}
STORY_TYPES = [*RUBRICS, "Random"]

# Child-first storytelling adapts each genre; it does not add a universal twist/danger rule.
CHILD_CATEGORY_RULES = {
    "Suspense": "Build child-friendly anticipation around one visible unknown; make waiting exciting, not confusing.",
    "Thriller": "Use a harmless race against time, playful chase or urgent task with clear obstacles; a child should root for success.",
    "Mystery": "Use concrete clues a child can notice and connect; clearly show how they solve one simple puzzle.",
    "Horror": "Use gentle pretend spookiness and one easy-to-understand fantastical threat; no traumatic terror or harm.",
    "Science Fiction": "Give one impossible object or power one simple visible rule; show what it does instead of teaching science.",
    "Crime": "Use an intentional harmless trick or playful theft; show simple clues, the culprit and a fair understandable outcome.",
    "Funny": "Use visible silly actions, connected mishaps and funny reactions; end with a clear visual joke a child can laugh at, not sarcasm.",
    "Emotional": "Use a familiar feeling such as missing a friend, feeling left out or receiving kindness; show a clear caring action and warm payoff.",
    "Inspirational": "Show a small relatable struggle, trying again or helping each other; let a visible success bring hope without a lecture.",
    "Dark Twist": "Make the dark flavor a mild mischievous or eerie surprise, never cruelty; a child must immediately understand what changed.",
    "Mystery with Final Twist": "Plant simple visible clues and finish with an obvious surprising connection that makes the opening clear to a child.",
}


def canonical_category(value: str) -> str:
    for category in STORY_TYPES:
        if category.casefold() == value.casefold():
            return category
    raise ValueError("Choose one of the displayed story categories")


def general_audience(settings) -> bool:
    """Viral Material is real factual content for the broad internet, not the child-first
    fiction default, and does not use the fixed fiction rubric categories below at all."""
    return settings.raw.get("dashboard_brief", {}).get("video_mode") == "viral"


def selected_category(settings) -> str:
    brief = settings.raw.get("dashboard_brief", {})
    if general_audience(settings):
        # Viral Material has no fixed pre-selected category: the model chooses its own
        # real-content angle for the actual topic and reports it back as story_category.
        # Before that first report exists, there is nothing to canonicalize against RUBRICS.
        return brief.get("selected_category", "") or ""
    category = canonical_category(brief.get("selected_category", brief.get("story_type", DEFAULT_CATEGORY)))
    if category == "Random":
        raise ValueError("Random must be resolved and saved before any request")
    return category


def estimated_seconds(word_count: int) -> float:
    """Transparent 135-words/minute planning estimate, not measured audio timing."""
    return round(word_count / 135 * 60, 2)


REAL_CONTENT_RULES = (
    "This is REAL, factual content, not fiction, locked to one specific niche (see the niche "
    "instructions below). Set story_category to a short label (3-60 characters) for whichever "
    "real-content angle within that niche genuinely fits. Do not force a fictional genre's "
    "structure (twists, suspense, invented danger) onto it unless the real facts genuinely "
    "include that. Danger, suspense and twists are never a requirement here."
)


def category_contract(settings) -> str:
    if general_audience(settings):
        return REAL_CONTENT_RULES
    category = selected_category(settings)
    rules, scores = RUBRICS[category]
    return (f"Selected category: {category}. Follow ONLY this category's rubric.\n{rules}\n"
            f"Child-first treatment (ages 8-10; takes precedence over adult genre intensity): {CHILD_CATEGORY_RULES[category]}\n"
            "Do not add requirements from other categories. Danger, suspense, horror and twists are not global requirements.\n"
            f"Relevant category-specific score names (exactly these): {', '.join(scores)}.")


def local_category_check(text: str, category: str) -> dict:
    """Conservative textual evidence, not another reviewer or a semantic guarantee."""
    result = {"category": category, "errors": [], "evidence": [], "score_cap": None,
              "method": "Free local evidence check; intent, clues and solution still need human judgment."}
    if category != "Crime":
        return result
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    ending = sentences[max(0, int(len(sentences) * .6)):]
    intention = r"\b(?:on purpose|deliberately|intentionally|tricked|stole|stolen|steal|prank|secret plan|to trick|to fool)\b"
    accident = r"\b(?:accident(?:ally)?|mistake|forgot|forgotten|by chance|lost the key)\b"

    def evidence(lines, pattern):
        found = []
        for line in lines:
            for match in re.finditer(pattern, line, re.IGNORECASE):
                before = line[max(0, match.start() - 40):match.start()]
                if re.search(r"\b(?:not|never|no one|nobody|wasn't|wasn’t|didn't|didn’t)\b[^.!?,;]*$", before, re.IGNORECASE):
                    continue
                found.append(line)
                break
        return found

    accidental_ending = evidence(ending, accident)
    resolved_intent = evidence(ending, intention)
    intent = evidence(sentences, intention)
    if accidental_ending and not resolved_intent:
        result["errors"].append("Crime category mismatch: the ending resolves an accident or mistake, not intentional harmless wrongdoing.")
        result["evidence"] = accidental_ending
    elif not intent:
        result["errors"].append("Crime category needs clear intentional harmless wrongdoing, connected clues and a solution; a missing object alone is insufficient.")
    if result["errors"]:
        result["score_cap"] = 3
    return result
