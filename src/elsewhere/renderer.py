from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .captions import scene_durations, write_ass, write_srt
from .config import Settings
from .languages import caption_font
from .models import NarrationAlignment, StoryPackage


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        safe_command = " ".join(command)
        raise RuntimeError(f"Command failed: {safe_command}\n{result.stderr[-3000:]}")


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def media_duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def create_silence(destination: Path, duration: float) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )


def create_local_narration(text: str, destination: Path) -> None:
    """Create a free macOS voice fixture that exercises the real audio render path."""
    aiff = destination.with_suffix(".aiff")
    run(["say", "-v", "Samantha", "-r", "100", "-o", str(aiff), text])
    try:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(aiff),
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ]
        )
    finally:
        aiff.unlink(missing_ok=True)


def normalize_narration(source: Path, destination: Path, minimum: float, maximum: float) -> float:
    source_duration = media_duration(source)
    if minimum <= source_duration <= maximum:
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return source_duration
    target = (minimum + maximum) / 2
    tempo = source_duration / target
    # atempo preserves pitch; real observed takes needed 13-22% correction and
    # still sounded natural, so the safe local-stretch window is wider than a
    # narrow +/-10% guess. Beyond this, a paid re-take (see Pipeline) is tried first.
    if not 0.75 <= tempo <= 1.25:
        raise ValueError(
            f"Narration is {source_duration:.2f}s; correction to {target:.1f}s would sound unnatural"
        )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-af",
            f"atempo={tempo:.6f}",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    duration = media_duration(destination)
    if not minimum <= duration <= maximum:
        raise ValueError(f"Locally adjusted narration is still out of range: {duration:.2f}s")
    return duration


def synthetic_alignment(story: StoryPackage, duration: float) -> NarrationAlignment:
    words = story.narration.split()
    unit = duration / len(words)
    from .models import TimedWord

    return NarrationAlignment(
        model="offline-synthetic-timing",
        transcript=story.narration,
        duration_seconds=duration,
        words=[
            TimedWord(word=word, start=index * unit, end=(index + 1) * unit)
            for index, word in enumerate(words)
        ],
    )


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_video(
    story: StoryPackage,
    images: list[Path],
    narration: Path,
    alignment: NarrationAlignment,
    settings: Settings,
    job_dir: Path,
    *, progress=None, check_cancel=None,
) -> Path:
    if len(images) != len(story.scenes):
        raise ValueError("The number of images must match the number of scenes")
    spec = settings.formats[story.format]
    width, height, fps = int(spec["width"]), int(spec["height"]), int(spec["fps"])
    duration = media_duration(narration)
    if abs(duration - alignment.duration_seconds) > 0.25:
        raise ValueError("Alignment duration does not match measured narration duration")
    durations = scene_durations(story, alignment)
    if progress:
        progress("captions", "running", "Building two-line captions from word timestamps")
    write_srt(story.narration, alignment, int(spec["caption_words"]), job_dir / "captions.srt")
    write_ass(story.narration, alignment, int(spec["caption_words"]), job_dir / "captions.ass", font_name=caption_font(settings))
    if progress:
        progress("captions", "completed", "Captions saved")
        progress("rendering", "running", "Rendering locally; no API cost")
    clips: list[Path] = []
    for index, (image, seconds) in enumerate(zip(images, durations, strict=True)):
        if check_cancel:
            check_cancel()
        clip = job_dir / f"clip-{index + 1:02d}.mp4"
        frames = max(1, round(seconds * fps))
        zoom = (
            "min(zoom+0.00055,1.07)"
            if index % 2 == 0
            else "if(lte(zoom,1.0),1.07,max(1.0,zoom-0.00055))"
        )
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},zoompan=z='{zoom}':d={frames}:s={width}x{height}:fps={fps},"
            "format=yuv420p"
        )
        run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(image),
                "-frames:v",
                str(frames),
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ]
        )
        clips.append(clip)
    concat_file = job_dir / "clips.txt"
    concat_file.write_text("\n".join(f"file '{clip.name}'" for clip in clips), encoding="utf-8")
    silent_video = job_dir / "silent.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent_video),
        ]
    )
    subtitle_ass = job_dir / "captions.ass"
    output = job_dir / "final.mp4"
    ass_filter = (
        f"ass='{_escape_filter_path(subtitle_ass)}':fontsdir='/System/Library/Fonts'"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(narration),
            "-filter_complex",
            (
                f"[0:v]{ass_filter}[v];"
                "[1:a]highpass=f=70,lowpass=f=14500,loudnorm=I=-14:TP=-1.5:LRA=8,"
                "apad=pad_dur=0.25,aresample=48000[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    if progress:
        progress("rendering", "completed", "MP4 saved")
    return output


def validate_video(
    video: Path,
    settings: Settings,
    story: StoryPackage,
    alignment: NarrationAlignment,
) -> dict:
    info = probe(video)
    spec = settings.formats[story.format]
    video_streams = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in info["streams"] if stream["codec_type"] == "audio"]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise ValueError("Final video must have exactly one video and one audio stream")
    video_stream, audio_stream = video_streams[0], audio_streams[0]
    duration = float(info["format"]["duration"])
    expected_scene_durations = scene_durations(story, alignment)
    clip_files = sorted(video.parent.glob("clip-*.mp4"))
    clip_durations = [media_duration(path) for path in clip_files]
    scene_clip_timing_ok = len(clip_durations) == len(expected_scene_durations) and all(
        abs(actual - expected) <= (1 / int(spec["fps"]) + 0.03)
        for actual, expected in zip(clip_durations, expected_scene_durations, strict=True)
    )
    ass_path = video.parent / "captions.ass"
    ass_text = ass_path.read_text(encoding="utf-8")
    dialogue = [line.split(",", 9)[9] for line in ass_text.splitlines() if line.startswith("Dialogue:")]
    caption_lines = [segment for item in dialogue for segment in item.split(r"\N")]
    checks = {
        "dimensions_1080x1920": (
            int(video_stream["width"]) == int(spec["width"])
            and int(video_stream["height"]) == int(spec["height"])
        ),
        "video_codec_h264": video_stream["codec_name"] == "h264",
        "frame_rate_30fps": video_stream["r_frame_rate"] == "30/1",
        "audio_codec_aac": audio_stream["codec_name"] == "aac",
        "duration_within_requested_range": (
            float(spec["duration_min_seconds"]) <= duration <= float(spec["duration_max_seconds"])
        ),
        "duration_matches_alignment": abs(duration - alignment.duration_seconds) <= 0.25,
        "eight_scene_images": len(story.scenes) == 8,
        "seven_scene_transitions": len(clip_files) - 1 == 7,
        "scene_clips_follow_measured_boundaries": scene_clip_timing_ok,
        "audio_sample_rate_48khz": int(audio_stream["sample_rate"]) == 48000,
        "caption_mobile_safe_style": (
            f"MobileSafe,{caption_font(settings)},60" in ass_text
            and ",110,110,360,1" in ass_text
            and bool(dialogue)
        ),
        "caption_lines_within_width_bound": all(
            len(line) <= 28 for line in caption_lines
        ),
    }
    volume = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    mean_match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", volume.stderr)
    max_match = re.search(r"max_volume:\s*(-?[\d.]+) dB", volume.stderr)
    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None
    checks["narration_audible"] = mean_db is not None and mean_db > -35.0
    checks["narration_not_clipped"] = max_db is not None and -12.0 < max_db <= -0.1
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError("Final validation failed: " + ", ".join(failed))
    return {
        "checked_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "checks": checks,
        "measured": {
            "duration_seconds": duration,
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
            "video_codec": video_stream["codec_name"],
            "audio_codec": audio_stream["codec_name"],
            "frame_rate": video_stream["r_frame_rate"],
            "audio_sample_rate": int(audio_stream["sample_rate"]),
            "audio_mean_volume_db": mean_db,
            "audio_max_volume_db": max_db,
            "scene_clip_durations_seconds": clip_durations,
            "aligned_scene_durations_seconds": expected_scene_durations,
        },
        "human_inspection_required": [
            "story originality and copyright safety",
            "character/style consistency across images",
            "caption legibility and exact visual placement on representative mobile devices",
            "narration naturalness and ending completeness",
            "scene transition aesthetics",
            "YouTube policy and monetization eligibility",
        ],
    }
