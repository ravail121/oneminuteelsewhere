"""Local readability + corpus familiarity + limited syntax/context screening.

There is no banned-vocabulary list and no replacement table. Frequency is an
adult-corpus proxy, not a validated child-comprehension score. Names are exempt.
"""
from __future__ import annotations

import re
from functools import lru_cache
from itertools import pairwise

from wordfreq import zipf_frequency

from .readability import WORDS, Readability, measure, syllables

NAMES = {"messi", "ronaldo", "lionel", "cristiano", "ishowspeed", "mrbeast", "beast"}
LIMITS = {"words_min": 115, "generation_words_max": 130, "words_max": 135,
          "flesch_reading_ease_min": 85, "flesch_kincaid_grade_max": 4,
          "average_sentence_words_max": 10, "maximum_sentence_words": 14,
          "estimated_seconds_max": 60}


def sentence_spans(text):
    for match in re.finditer(r'''[^.!?]+(?:[.!?]+[”"']*|$)''', text):
        raw = match.group()
        start = match.start() + len(raw) - len(raw.lstrip())
        end = match.end() - len(raw) + len(raw.rstrip())
        if WORDS.search(text[start:end]):
            yield start, end, text[start:end]


def highlights(text, issues):
    spans = [i for i in issues if i.get("start") is not None]
    points = sorted({0, len(text), *(i["start"] for i in spans), *(i["end"] for i in spans)})
    result = []
    for start, end in pairwise(points):
        reasons = list(dict.fromkeys(i["message"] for i in spans if i["start"] < end and i["end"] > start))
        result.append({"text": text[start:end], "highlight": bool(reasons), "reason": " ".join(reasons)})
    return result


@lru_cache(maxsize=256)
def analyze(text: str) -> dict:
    issues = []
    has_words = bool(WORDS.search(text))
    metrics = (measure(text, familiar_names=True) if has_words else
               Readability(0, 0, 0, 0, 0, 0, method="No English words to measure")).to_dict()
    sentences = list(sentence_spans(text))
    maximum = max((len(WORDS.findall(s)) for _, _, s in sentences), default=0)

    def issue(kind, message, start=None, end=None, **details):
        issues.append({"kind": kind, "message": message, "start": start, "end": end,
                       "text": text[start:end] if start is not None else "", **details})

    if not has_words:
        issue("missing_story", "No spoken English words found. Add a clear English story.", 0, len(text))
    if not 115 <= metrics["words"] <= 135:
        issue("word_count", f"Story has {metrics['words']} words; use 115–135 (generation target 115–130).")
    checks = [("flesch_reading_ease", 85, metrics["flesch_reading_ease"] < 85, "at least"),
              ("flesch_kincaid_grade", 4, metrics["flesch_kincaid_grade"] > 4, "no higher than"),
              ("average_sentence_words", 10, metrics["average_sentence_words"] > 10, "no higher than")]
    labels = {"flesch_reading_ease": "Flesch Reading Ease", "flesch_kincaid_grade": "Flesch-Kincaid grade",
              "average_sentence_words": "average sentence length"}
    for key, limit, failed, direction in checks:
        if failed:
            issue(key, f"{labels[key]} is {metrics[key]:.2f}; needs to be {direction} {limit}. Use simpler wording and clearer sentences.")
    duration = metrics["words"] / 135 * 60
    if duration > 60:
        issue("duration", f"Estimated duration exceeds 60 seconds ({duration:.1f}s at 135 words/minute).")
    for index, (start, end, sentence) in enumerate(sentences, 1):
        tokens = list(WORDS.finditer(sentence))
        length = len(tokens)
        if length > 14:
            issue("sentence_length", f"Sentence {index} has {length} words; maximum 14. Split it into clear ideas.", start, end)
        # Structural cues, not a vocabulary blacklist: stacked dependent clauses,
        # multiple joined ideas, or a passive construction with an explicit agent.
        joins = len(re.findall(r"\b(?:although|because|while|unless|which|whereas|despite)\b", sentence, re.IGNORECASE))
        # Commas alone do not imply several ideas: an introductory word plus
        # a simple list ("Soon, ... flour, sugar, and chairs") is easy English.
        complexity = joins >= 2 or sentence.count(';') > 0
        if complexity:
            issue("sentence_structure", f"Sentence {index} may contain several linked ideas. Put events in order, one main idea per sentence.", start, end)
        if re.search(r"\b(?:was|were|is|are|been)\s+\w+(?:ed|en)\s+by\b", sentence, re.IGNORECASE):
            issue("passive_voice", f"Sentence {index} uses a passive construction. Name who does the action first where possible.", start, end)
        for token in tokens:
            word = token.group()
            normalized = word.lower().replace("’", "'").removesuffix("'s")
            if normalized in NAMES:
                continue
            frequency = zipf_frequency(normalized, "en", wordlist="large")
            syllable_count = syllables(word)
            # Easy short context lowers concern; a nearby plain-language definition
            # lowers it further. Neither repeated use nor frequency alone bans a word.
            following = sentence[token.end():]
            definition = re.match(r"\s*(?:,\s*(?:a|an)\b|\(\s*|means\b|is\s+(?:a|an)\b)", following, re.IGNORECASE)
            explanation = WORDS.findall(following)[:8] if definition else []
            explained = bool(explanation) and all(zipf_frequency(w.lower(), "en") >= 4.0 for w in explanation)
            difficulty = (max(0, 4.5 - frequency) * 2 + max(0, syllable_count - 2) * .7
                          + max(0, len(normalized) - 8) * .12 + (0.7 if complexity else -0.3)
                          - (1.5 if explained else 0))
            if frequency < 4.5 and difficulty >= 2.4:
                issue("word_familiarity", f'“{word}” in sentence {index} may be unfamiliar (Zipf frequency {frequency:.2f}, '
                      f'{syllable_count} syllables). Use simpler wording or a short, clear explanation.',
                      start + token.start(), start + token.end(), frequency=frequency, contextual_difficulty=round(difficulty, 2))
    return {"version": 1, "passed": not issues, "result": "Easy enough for children" if not issues else "Simpler wording needed",
            "metrics": metrics, "maximum_sentence_words": maximum, "estimated_seconds": round(duration, 2),
            "targets": LIMITS, "issues": issues, "segments": highlights(text, issues),
            "method": "Offline wordfreq 3.1.1 frequency data, heuristic syllables, sentence structure and nearby definitions; cast names exempt.",
            "limitation": "Screening estimate, not a guarantee of comprehension. Context, metaphors, chronology and entertainment still need human listening."}
