"""Offline pacing repair using already-paid speech and measured source word timing.

No API client is imported. Words/audio are never rewritten or regenerated. A modest
pitch-preserving slowdown and explicit pauses are mapped back into word timestamps.
"""
from __future__ import annotations

import argparse
import json
import wave
from itertools import pairwise
from pathlib import Path

from elsewhere.captions import _token, align_story_words
from elsewhere.config import load_settings
from elsewhere.costs import CostLedger
from elsewhere.models import NarrationAlignment, StoryPackage, TimedWord
from elsewhere.openai_service import save_json
from elsewhere.renderer import media_duration, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    job = args.job_dir.resolve()
    plan = json.loads(args.plan.read_text())
    settings = load_settings(args.config)
    story = StoryPackage.model_validate_json((job / "story.json").read_text())
    source = NarrationAlignment.model_validate_json((job / "source-alignment.json").read_text())
    tokens = story.narration.split()
    if [_token(word) for word in tokens] != [_token(word.word) for word in source.words]:
        raise ValueError("Pacing requires exact source transcript coverage; inspect discrepancies first")
    words = align_story_words(story.narration, source)
    if any(right.start < left.end - .04 for left, right in pairwise(words)):
        raise ValueError("Source word timestamps overlap; inspect before pacing")
    tempo = float(plan["tempo"])
    target = float(plan["target_seconds"])
    if not .90 <= tempo <= 1.10:
        raise ValueError("Pacing permits at most ten percent speech tempo adjustment")
    if not 52 <= target <= 60:
        raise ValueError("Target must stay within this test's requested range")
    destination, aligned = job / "narration.wav", job / "alignment.json"
    if destination.exists() or aligned.exists():
        raise ValueError("Paced assets already exist; do not overwrite them")
    base = job / "narration-tempo.wav"
    if not base.exists():
        run(["ffmpeg", "-y", "-i", str(job / "narration-source.wav"), "-af", f"atempo={tempo}",
             "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(base)])
    with wave.open(str(base), "rb") as audio:
        params, pcm = audio.getparams(), audio.readframes(audio.getnframes())
    rate = params.framerate
    frame_bytes = params.sampwidth * params.nchannels
    base_duration = params.nframes / rate
    factor = base_duration / source.duration_seconds
    extra = target - base_duration
    fixed = sum(float(p.get("seconds", 0)) for p in plan["pauses"])
    weights = sum(float(p.get("weight", 0)) for p in plan["pauses"])
    if extra < fixed or weights <= 0:
        raise ValueError("Pause plan does not fit requested duration")
    cuts = []
    for pause in plan["pauses"]:
        index = int(pause["after_word"])
        if not 0 < index < len(words):
            raise ValueError("Pause must fall between spoken words")
        if tokens[index - 1] != pause["expected_word"]:
            raise ValueError("Pause plan does not match exact script")
        boundary = (words[index - 1].end + words[index].start) / 2 * factor
        duration = float(pause.get("seconds", 0)) + float(pause.get("weight", 0)) / weights * (extra - fixed)
        cuts.append((round(boundary * rate), round(duration * rate), index))
    cuts.sort()
    temporary = destination.with_suffix(".wav.part")
    with wave.open(str(temporary), "wb") as audio:
        audio.setparams(params)
        cursor = 0
        for cut, silence, _ in cuts:
            audio.writeframes(pcm[cursor * frame_bytes:cut * frame_bytes])
            audio.writeframes(bytes(silence * frame_bytes))
            cursor = cut
        audio.writeframes(pcm[cursor * frame_bytes:])
    temporary.replace(destination)
    actual_duration = media_duration(destination)
    mapped = []
    for index, (token, word) in enumerate(zip(tokens, words)):
        offset = sum(silence / rate for _, silence, after_word in cuts if index >= after_word)
        mapped.append(TimedWord(word=token, start=word.start * factor + offset, end=word.end * factor + offset))
    result = NarrationAlignment(model=source.model + "+local-measured-pacing", transcript=story.narration,
                                duration_seconds=actual_duration, words=mapped)
    save_json(aligned, result)
    save_json(job / "pacing-report.json", {
        "source_seconds": source.duration_seconds, "tempo": tempo, "tempo_base_seconds": base_duration,
        "final_seconds": actual_duration, "added_pause_seconds": sum(s / rate for _, s, _ in cuts),
        "source_transcript_exact_match": True, "word_timing": "Measured source times transformed through local audio edits",
        "plan": plan, "human_listening_required": True,
    })
    ledger = CostLedger(job / "cost-report.json", .50, settings.costs)
    ledger.add_local_stage("narration_pacing", "Saved speech slowed by 10 percent; measured sentence/comma pauses inserted; no API call")
    print(f"Saved {actual_duration:.3f}s narration and {len(mapped)} transformed measured word timestamps")


if __name__ == "__main__":
    main()
