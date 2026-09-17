"""Free deterministic sentence-level direction for a single narrator/TTS request."""
import hashlib
import re

from .disclosure import require_content_only
from .rubrics import selected_category

TONES = {
    "Suspense": "curious and restrained", "Thriller": "urgent and alert", "Mystery": "curious",
    "Horror": "hushed and uneasy", "Science Fiction": "wonder", "Crime": "curious and focused",
    "Funny": "playful and energetic", "Emotional": "warm and tender", "Inspirational": "hopeful",
    "Dark Twist": "quiet unease", "Mystery with Final Twist": "curious suspense",
}


def make_plan(text, settings):
    require_content_only(text)
    category = selected_category(settings)
    sentences = [s.strip() for s in re.findall(r'.+?(?:[.!?।؟]+[”"\']*(?=\s|$)|$)', text, flags=re.DOTALL) if s.strip()]
    if " ".join(sentences).split() != text.split():
        raise ValueError("Narration plan cannot alter the approved text")
    lines = []
    for sentence in sentences:
        lower = sentence.lower()
        speaker = "Narrator"
        name = r"(Messi|Ronaldo|IShowSpeed|Mr\.?\s*Beast)"
        match = re.search(rf"\b{name}\s+(?:said|asked|whispered|shouted|replied)", sentence)
        if not match:
            match = re.search(rf"\b(?:said|asked|whispered|shouted|replied)\s+{name}\b", sentence)
        if match:
            token = re.sub(r"[.\s]", "", match[1]).lower()
            speaker = {"messi": "Lionel Messi", "ronaldo": "Cristiano Ronaldo",
                       "ishowspeed": "IShowSpeed", "mrbeast": "MrBeast"}[token]
        # Viral Material has no fixed rubric tone (its category is a free-form real-content
        # angle, or not yet known before generation); default to a clear, confident delivery.
        emotion, intensity, speed, pause = TONES.get(category, "clear and confident"), "moderate", 1.0, .18
        if category == "Funny":
            speed = 1.06
        if category == "Thriller" and re.search(r"\b(run|ran|rush|rushed|quick|hurry)\b", lower):
            speed = 1.12
        if re.search(r"\b(whisper|whispered|secret|hush|afraid|frightened)\b", lower):
            emotion, intensity, speed = "whisper, clear and audible", "soft", .96
        elif re.search(r"\b(shout|shouted|hurry|stop|look out)\b", lower) and ('!' in sentence or speaker != "Narrator"):
            emotion, intensity = "controlled urgent shout, no distortion", "firm"
        elif re.search(r"\b(sad|cried|tears|missed|sorry|thank|meant)\b", lower) or category == "Emotional":
            emotion, intensity, speed = "soft and meaningful", "soft", .94
        if '?' in sentence and category in {"Mystery", "Suspense", "Mystery with Final Twist", "Dark Twist"}:
            pause = .4
        lines.append({"sentence_text": sentence, "speaker": speaker, "emotion": emotion,
                      "intensity": intensity, "speed": speed, "pause_after_sentence": pause})
    if len(lines) > 1:
        lines[-2]["pause_after_sentence"] = .5
    if lines:
        lines[-1].update(emotion=lines[-1]["emotion"] + "; emphasize final sentence", speed=.96)
    return {"schema_version": 1, "source": "free local sentence heuristics; human listening required",
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "category": category,
            "model": "gpt-4o-mini-tts", "voice": settings.models["voice"], "sentences": lines,
            "speech_requests": 1, "voice_imitation": False}


def instructions(plan, base):
    lines = plan["sentences"]
    # Bound instruction input; retain the complete plan on disk. Highlight the ending
    # and the most expressive dialogue instead of sending unlimited repeated directions.
    chosen = sorted(range(len(lines)), key=lambda i: (i == len(lines)-1, lines[i]["speaker"] != "Narrator",
                    lines[i]["intensity"] != "moderate", i == 0), reverse=True)[:12]
    directions = "\n".join(f'{i+1}. {lines[i]["sentence_text"]} | {lines[i]["speaker"]}: {lines[i]["emotion"]}; '
        f'{lines[i]["intensity"]}; speed {lines[i]["speed"]}x; pause after {lines[i]["pause_after_sentence"]}s.'
        for i in sorted(chosen))
    return (base + "\nOne continuous performance using ONLY the selected narrator voice. All speaker names are "
            "fictional dialogue attribution, NOT voice identities. Never imitate Lionel Messi, Cristiano Ronaldo, "
            "IShowSpeed or MrBeast, their accents, or their real voices. Do not read these directions aloud. Read the input verbatim. "
            "Emote naturally: controlled urgent shouts when warranted, audible whispers, tender soft lines, "
            "energetic comedy and faster action. Smooth volume changes, no clipping or extreme volume. "
            "Pause before mysteries and final reveals where appropriate; emphasize the final sentence. "
            "Sentence directions are expressive targets, not guaranteed timestamps.\n" + directions)
