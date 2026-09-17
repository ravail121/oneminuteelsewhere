"""Offline English readability estimates; no network or external model required.

Contractions count as one word; hyphenated words count separately. Sentence
boundaries use . ! ? (including punctuation before closing quotes). Syllables
use a deterministic vowel-group heuristic, so scores are estimates, not a
claim about every listener's comprehension. No score is clamped to 0..100.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

WORDS = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
SYLLABLE_EXCEPTIONS = {
    "every": 2, "everyone": 3, "everything": 3, "someone": 2,
    "something": 2, "anyone": 3, "anything": 3, "our": 1,
    "hour": 1, "hours": 1, "fire": 1, "quiet": 2, "quietly": 3,
    "people": 2, "science": 2, "scientific": 4, "real": 1,
    "realized": 3, "different": 3, "business": 2, "being": 2,
    "does": 1, "doesn't": 2, "couldn't": 2, "wouldn't": 2,
    "shouldn't": 2, "isn't": 2, "wasn't": 2, "didn't": 2,
}


def syllables(word: str) -> int:
    word = word.lower().replace("’", "'")
    if word in SYLLABLE_EXCEPTIONS:
        return SYLLABLE_EXCEPTIONS[word]
    word = word.replace("'", "")
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ye")):
        count -= 1
    if (word.endswith("ed") and not word.endswith(("ted", "ded"))
            and len(word) > 3 and word[-3] not in "aeiouy"):
        count -= 1
    if (word.endswith("es") and not word.endswith(("ses", "xes", "zes", "ches", "shes"))
            and len(word) > 3 and word[-3] not in "aeiouy"):
        count -= 1
    return max(1, count)


@dataclass(frozen=True)
class Readability:
    words: int
    sentences: int
    syllables: int
    average_sentence_words: float
    flesch_reading_ease: float
    flesch_kincaid_grade: float
    method: str = "offline vowel-group syllable estimate v1"

    def to_dict(self) -> dict:
        return asdict(self)


def measure(text: str, *, familiar_names: bool = False) -> Readability:
    words = WORDS.findall(text)
    sentences = sum(bool(WORDS.search(part)) for part in re.split(r"[.!?]+", text))
    if not words or not sentences:
        raise ValueError("Readability requires spoken words and sentences")
    names = {"messi", "ronaldo", "lionel", "cristiano", "ishowspeed", "mrbeast", "beast"} if familiar_names else set()
    count = sum(1 if word.lower().removesuffix("'s").removesuffix("’s") in names else syllables(word) for word in words)
    length = len(words) / sentences
    per_word = count / len(words)
    return Readability(
        len(words), sentences, count, length,
        206.835 - 1.015 * length - 84.6 * per_word,
        0.39 * length + 11.8 * per_word - 15.59,
        method="offline syllable estimate; cast names treated as familiar one-syllable words" if familiar_names else "offline vowel-group syllable estimate v1",
    )
