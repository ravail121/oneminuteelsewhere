from __future__ import annotations

import argparse
from pathlib import Path

import mlx_whisper

from elsewhere.captions import align_story_words
from elsewhere.config import load_settings
from elsewhere.costs import CostLedger
from elsewhere.models import NarrationAlignment, StoryPackage, TimedWord
from elsewhere.openai_service import save_json
from elsewhere.renderer import media_duration


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover word timing locally without repeating a paid transcription request"
    )
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--model", default="mlx-community/whisper-tiny")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    job_dir = args.job_dir.resolve()
    story = StoryPackage.model_validate_json(
        (job_dir / "story.json").read_text(encoding="utf-8")
    )
    narration = job_dir / "narration.wav"
    duration = media_duration(narration)
    result = mlx_whisper.transcribe(
        str(narration),
        path_or_hf_repo=args.model,
        language="en",
        initial_prompt=story.narration,
        word_timestamps=True,
        condition_on_previous_text=True,
        verbose=None,
    )
    words: list[TimedWord] = []
    for segment in result["segments"]:
        for item in segment.get("words", []):
            start = max(0.0, float(item["start"]))
            end = min(duration, float(item["end"]))
            if end <= start:
                end = min(duration, start + 0.02)
            if end <= start:
                start = max(0.0, duration - 0.02)
                end = duration
            words.append(TimedWord(word=item["word"].strip(), start=start, end=end))
    alignment = NarrationAlignment(
        model=f"local:{args.model}",
        transcript=str(result["text"]).strip(),
        duration_seconds=duration,
        words=words,
    )
    matched = align_story_words(story.narration, alignment)
    save_json(job_dir / "alignment.json", alignment)

    settings = load_settings(args.config)
    report = job_dir / "cost-report.json"
    ledger = CostLedger(report, float(settings.costs["default_allowance_usd"]), settings.costs)
    ledger.add_local_stage(
        "caption_alignment_recovery",
        f"Local {args.model} word timing recovered from saved narration; no API call",
    )
    print(
        f"saved {len(words)} recognized words; aligned {len(matched)} story words "
        f"across {duration:.3f}s"
    )


if __name__ == "__main__":
    main()
