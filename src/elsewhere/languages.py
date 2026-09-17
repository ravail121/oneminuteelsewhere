"""Explicit supported narration/caption languages; English metrics never grade other scripts."""
import re
import unicodedata
from pathlib import Path

LANGUAGES = {"en": "English", "hi": "Hindi (हिन्दी)", "ta": "Tamil (தமிழ்)", "ur": "Urdu (اردو)"}
COUNTRIES = {"IN": "India", "PK": "Pakistan", "US": "United States", "GB": "United Kingdom"}
# Each language lists (font name, absolute path) candidates in preference order across
# platforms; the first one whose file actually exists on this machine is used for both the
# pre-flight check and the caption render, so the two can never disagree about which font
# is really installed. macOS system fonts first, then the equivalent apt-installable fonts
# (fonts-dejavu-core, fonts-noto-core, fonts-noto-extra) for a Linux deployment.
FONTS = {
    "en": [("Helvetica", "/System/Library/Fonts/Helvetica.ttc"),
           ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")],
    "hi": [("Devanagari Sangam MN", "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"),
           ("Noto Sans Devanagari", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf")],
    "ta": [("Tamil Sangam MN", "/System/Library/Fonts/Supplemental/Tamil Sangam MN.ttc"),
           ("Noto Sans Tamil", "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf")],
    "ur": [("Noto Nastaliq Urdu", "/System/Library/Fonts/NotoNastaliq.ttc"),
           ("Noto Nastaliq Urdu", "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf")],
}


def _resolved_font(code):
    for name, path in FONTS.get(code, []):
        if Path(path).is_file():
            return name, path
    return None


def language(settings):
    return settings.brand.get("language", "en")


def suggest_language(topic):
    """Topic-only, free heuristics. The feed country is deliberately not an input."""
    if re.search(r"[\u0b80-\u0bff]|\b(tamil|chennai|tamil nadu)\b", topic, re.IGNORECASE):
        return "ta", "The topic mentions Tamil context or uses Tamil writing; Tamil suggested. You can override it."
    if re.search(r"[\u0900-\u097f]", topic):
        return "hi", "The topic uses Devanagari writing; Hindi suggested, but other languages share this script. You can override it."
    if re.search(r"[\u0600-\u06ff]", topic):
        return "ur", "The topic uses Arabic-derived writing; Urdu is a tentative supported-language suggestion. Check and override if needed."
    hindi = bool(re.search(r"\b(hindi|india|indian|bollywood|mumbai|delhi|diwali|holi|shah rukh khan)\b", topic, re.IGNORECASE))
    urdu = bool(re.search(r"\b(urdu|pakistan|pakistani|lahore|karachi|islamabad)\b", topic, re.IGNORECASE))
    if hindi and not urdu:
        return "hi", "Indian/Hindi cultural context detected in the topic itself; Hindi suggested. Regional-language topics may need an override."
    if urdu and not hindi:
        return "ur", "Pakistani/Urdu context detected in the topic itself; Urdu suggested. You can override it."
    return "en", "No clear single supported-language context in the topic; English fallback. Feed country is ignored. You can override it."


def caption_font(settings):
    code = language(settings)
    if code not in FONTS:
        raise ValueError("Unsupported narration/caption language")
    resolved = _resolved_font(code)
    if resolved is None:
        raise ValueError("The selected language needs its local caption font installed before production")
    return resolved[0]


def check_font(code):
    if _resolved_font(code) is None:
        raise ValueError("The selected language needs its local caption font installed before production")


def unicode_token(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold()
                   if unicodedata.category(c)[0] in "LNM")


def analyze_language(text, code):
    if code == "en":
        from .easy_english import analyze
        return analyze(text)
    words = text.split()
    sentences = [s for s in re.split(r"[.!?।؟]+", text) if s.strip()]
    maximum = max((len(s.split()) for s in sentences), default=0)
    issues = []
    if not words:
        issues.append({"message": "Story narration is missing.", "text": ""})
    if len(words) > 135:
        issues.append({"message": "More than 135 space-separated words; timing needs a listening check.", "text": ""})
    if 0 < len(words) < 115:
        issues.append({"message": "Below the 115-word target; measured narration timing may differ in this language.", "text": ""})
    if len(words) / max(1, len(sentences)) > 10:
        issues.append({"message": "Average sentence exceeds ten space-separated words; use shorter, clearer sentences.", "text": ""})
    if maximum > 14:
        issues.append({"message": "A sentence exceeds 14 space-separated words; shorter sentences may be clearer.", "text": ""})
    return {"passed": not issues, "result": "Local script checks passed — human listening needed" if not issues else "Advisory language checks",
            "metrics": {"words": len(words), "sentences": len(sentences), "flesch_reading_ease": None,
                        "flesch_kincaid_grade": None, "average_sentence_words": len(words) / max(1, len(sentences))},
            "maximum_sentence_words": maximum, "estimated_seconds": round(len(words) / 135 * 60, 2),
            "segments": [{"text": text, "highlight": False}], "issues": issues,
            "method": "Local Unicode/space-separated word and sentence checks. English Flesch scores do not apply.",
            "limitation": "Provisional timing only; native-language clarity, pronunciation and captions require human inspection."}
