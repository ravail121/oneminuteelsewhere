"""Real ffmpeg render tests for the final playback-speed change. Skipped where ffmpeg or
macOS `say` (used only to produce real narration audio for the test) aren't available."""
import copy
import os
import shutil
from pathlib import Path

import pytest

from elsewhere.config import Settings, load_settings
from elsewhere.demo import create_demo_images, demo_story
from elsewhere.renderer import (
    PLAYBACK_SPEED,
    create_local_narration,
    media_duration,
    render_video,
    synthetic_alignment,
    validate_video,
)

ROOT = Path(__file__).resolve().parents[1]


def setup(tmp_path):
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    return Settings(tmp_path, raw)


def test_final_reel_plays_back_at_the_configured_speed(tmp_path, monkeypatch):
    # Homebrew's plain `ffmpeg` formula excludes libass, so the `ass` caption filter this
    # code relies on isn't available there — same reason test_viral.py's caption test
    # prefers ffmpeg-full. Not relevant in production (Ubuntu's apt ffmpeg includes libass).
    full_ffmpeg = Path("/opt/homebrew/opt/ffmpeg-full/bin")
    if full_ffmpeg.is_dir():
        monkeypatch.setenv("PATH", f"{full_ffmpeg}:{os.environ['PATH']}")
    if not shutil.which("ffmpeg") or not shutil.which("say"):
        pytest.skip("Needs ffmpeg (with libass) and macOS `say` for a real render")
    settings = setup(tmp_path)
    story = demo_story()
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    images = create_demo_images(story, settings, job_dir)
    narration = job_dir / "narration.wav"
    create_local_narration(story.narration, narration)
    original_duration = media_duration(narration)
    alignment = synthetic_alignment(story, original_duration)
    expected = original_duration / PLAYBACK_SPEED
    # Bracket the format's duration window around this real narration's expected post-speed
    # length (generous margin for real `say` timing variance) so validate_video's own
    # duration check exercises the speed math end to end, not just this test's own math.
    settings.raw["formats"]["short"]["duration_min_seconds"] = max(0.1, (expected - 3) * PLAYBACK_SPEED)
    settings.raw["formats"]["short"]["duration_max_seconds"] = (expected + 3) * PLAYBACK_SPEED

    output = render_video(story, images, narration, alignment, settings, job_dir)

    actual = media_duration(output)
    assert abs(actual - expected) <= 0.5, "Final video duration should be ~1/1.5x the narration"

    report = validate_video(output, settings, story, alignment)
    assert report["checks"]["duration_matches_alignment"]
    assert report["checks"]["duration_within_requested_range"]
    assert report["checks"]["narration_audible"]
