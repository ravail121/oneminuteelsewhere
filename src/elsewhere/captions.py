from __future__ import annotations

import re
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path

from .disclosure import require_content_only, require_story_content
from .languages import unicode_token
from .models import NarrationAlignment, StoryPackage, TimedWord


def chunks(text: str, max_words: int) -> list[str]:
    result: list[str] = []
    sentences = re.split(r"(?<=[.!?।؟])\s+", re.sub(r"\s+", " ", text).strip())
    for sentence in sentences:
        words = sentence.split()
        sentence_parts = [words[i : i + max_words] for i in range(0, len(words), max_words)]
        if len(sentence_parts) > 1 and len(sentence_parts[-1]) == 1:
            sentence_parts[-1].insert(0, sentence_parts[-2].pop())
        result.extend(" ".join(part) for part in sentence_parts if part)
    return result


def timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def ass_timestamp(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours, centiseconds = divmod(centiseconds, 360_000)
    minutes, centiseconds = divmod(centiseconds, 6_000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _token(value: str) -> str:
    return unicode_token(value)


def _two_lines(value: str, max_chars: int = 28) -> str:
    words = value.split()
    if len(value) <= max_chars and len(words) <= 4:
        return value
    candidates = []
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        overflow = max(0, len(left) - max_chars) + max(0, len(right) - max_chars)
        balance = abs(len(left) - len(right))
        candidates.append((overflow, balance, index, left, right))
    if not candidates:
        raise ValueError("A caption word exceeds the safe-area bound")
    _, _, _, left, right = min(candidates)
    if len(left) > max_chars or len(right) > max_chars:
        raise ValueError(f"Caption cannot fit inside mobile safe width: {value}")
    return left + "\n" + right


def align_story_words(text: str, alignment: NarrationAlignment) -> list[TimedWord]:
    original = text.split()
    spoken = alignment.words
    matcher = SequenceMatcher(
        None,
        [_token(word) for word in original],
        [_token(word.word) for word in spoken],
        autojunk=False,
    )
    mapped: dict[int, TimedWord] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapped[block.a + offset] = spoken[block.b + offset]
    matched_ratio = len(mapped) / max(1, len(original))
    if matched_ratio < 0.75:
        raise ValueError(f"Narration alignment matched only {matched_ratio:.0%} of story words")

    result: list[TimedWord | None] = [mapped.get(index) for index in range(len(original))]
    anchors = [-1] + sorted(mapped) + [len(original)]
    for left_index, right_index in pairwise(anchors):
        if right_index - left_index <= 1:
            continue
        start = 0.0 if left_index < 0 else mapped[left_index].end
        end = alignment.duration_seconds if right_index >= len(original) else mapped[right_index].start
        gap = max(0.01, end - start)
        count = right_index - left_index - 1
        unit = gap / count
        for offset, index in enumerate(range(left_index + 1, right_index)):
            result[index] = TimedWord(
                word=original[index],
                start=start + unit * offset,
                end=start + unit * (offset + 1),
            )
    if any(item is None for item in result):
        raise ValueError("Could not build a complete narration timing map")
    return [item for item in result if item is not None]


def caption_records(
    text: str, alignment: NarrationAlignment, max_words: int
) -> list[tuple[float, float, str]]:
    require_content_only(text)
    timed = align_story_words(text, alignment)
    parts = []
    for part in chunks(text, max_words):
        pending = part.split()
        while pending:
            count = len(pending)
            while count:
                try:
                    candidate = " ".join(pending[:count])
                    _two_lines(candidate)
                    break
                except ValueError:
                    count -= 1
            if not count:
                raise ValueError("A caption word exceeds the safe-area bound; no text was truncated")
            parts.append(candidate)
            pending = pending[count:]
    records: list[tuple[float, float, str]] = []
    cursor = 0
    for part in parts:
        count = len(part.split())
        words = timed[cursor : cursor + count]
        if not words:
            raise ValueError("Caption timing exceeded aligned narration")
        records.append((words[0].start, words[-1].end, _two_lines(part)))
        cursor += count
    if cursor != len(timed):
        raise ValueError("Caption text did not consume the complete narration")
    for index in range(len(records) - 1):
        start, _, text_part = records[index]
        records[index] = (start, records[index + 1][0], text_part)
    if records:
        start, _, text_part = records[-1]
        records[-1] = (start, alignment.duration_seconds, text_part)
    return records


def scene_durations(story: StoryPackage, alignment: NarrationAlignment) -> list[float]:
    require_story_content(story)
    timed = align_story_words(story.narration, alignment)
    boundaries = [0.0]
    cursor = 0
    for scene in story.scenes[:-1]:
        cursor += len(scene.narration.split())
        boundaries.append(timed[cursor - 1].end)
    boundaries.append(alignment.duration_seconds)
    durations = [end - start for start, end in pairwise(boundaries)]
    if any(value < 1.0 for value in durations):
        raise ValueError("Measured alignment produced a scene shorter than one second")
    return durations


def write_srt(
    text: str,
    alignment: NarrationAlignment,
    max_words: int,
    destination: Path,
) -> None:
    records = caption_records(text, alignment, max_words)
    payload = []
    for index, (start, end, part) in enumerate(records, 1):
        payload.append(f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{part}\n")
    destination.write_text("\n".join(payload), encoding="utf-8")


def write_ass(
    text: str,
    alignment: NarrationAlignment,
    max_words: int,
    destination: Path,
    *,
    font_name: str = "Helvetica",
) -> None:
    records = caption_records(text, alignment, max_words)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: MobileSafe,{font_name},60,&H00FFFFFF,&H00FFFFFF,&H00101018,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,110,110,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for start, end, part in records:
        escaped = (
            part.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\n", r"\N")
        )
        lines.append(
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},MobileSafe,,0,0,0,,{escaped}"
        )
    destination.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
