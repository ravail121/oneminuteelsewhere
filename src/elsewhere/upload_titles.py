"""Free Roman-script upload metadata. Never changes story or narration files."""
import re

from unidecode import unidecode

LABELS = {"Funny": "Funny Story", "Mystery": "Mystery Story", "Suspense": "Suspense Story",
          "Thriller": "Thriller Story", "Horror": "Scary Story", "Science Fiction": "Sci-Fi Story",
          "Crime": "Crime Mystery Story", "Emotional": "Emotional Story",
          "Inspirational": "Inspiring Story", "Dark Twist": "Dark Twist Story",
          "Mystery with Final Twist": "Mystery Twist Story"}


def roman_upload_title(title, category, *, viral=False):
    # Transliteration is approximate, not translation. The upload preview allows editing.
    hook = unidecode(title, errors="replace", replace_str=" ")
    hook = re.sub(r"#shorts\b", "", hook, flags=re.IGNORECASE)
    hook = re.sub(r"[<>\x00-\x1f\x7f]", " ", hook)
    hook = " ".join(hook.split()).strip(" |-")
    if viral:
        # Viral Material's category is a free-form real-content angle chosen by the model
        # (Recipe, Real Story, Space Facts, ...), not one of the fixed fiction genres below.
        label = "Viral " + ((category or "").strip() or "Trending")
    else:
        label = LABELS.get(category, "Original Story")
    suffix = f" | {label} #Shorts"
    hook = re.sub(r"\s*\|\s*" + re.escape(label) + r"$", "", hook, flags=re.IGNORECASE)
    budget = 100 - len(suffix)
    if len(hook) > budget:
        hook = hook[:budget].rsplit(" ", 1)[0] or hook[:budget]
    return (hook or "A New Story") + suffix
