from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import load_settings
from .pipeline import Pipeline
from .youtube import authorize


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="elsewhere")
    root.add_argument("--config", default="config.yaml")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local production prerequisites")
    commands.add_parser("cost-plan", help="Show the no-API cost plan for one Short")
    commands.add_parser("auth-youtube", help="Complete the one-time YouTube OAuth flow")
    commands.add_parser("scheduler", help="Run the permanent publishing scheduler")
    run = commands.add_parser("run", help="Generate, review, render, and optionally upload one video")
    run.add_argument("--format", choices=["short", "long"], default="short")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--upload", action="store_true")
    run.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    run.add_argument(
        "--fixture",
        choices=[
            "mirror",
            "matter-printer",
            "matter-printer-final",
            "eighth-shadow",
            "eighth-shadow-final",
        ],
    )
    run.add_argument("--resume", type=Path)
    run.add_argument("--story-file", type=Path, help="Exact local story package; review once, never generate a replacement")
    run.add_argument("--review-only", action="store_true", help="Stop after saving the independent review")
    run.add_argument("--max-cost-usd", type=float, default=2.0)
    run.add_argument(
        "--approve-paid",
        action="store_true",
        help="Explicit acknowledgement that this invocation may make paid API calls",
    )
    return root


def doctor(settings) -> int:
    filters = ""
    if shutil.which("ffmpeg"):
        filters = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    checks = {
        "Python 3.11+": sys.version_info >= (3, 11),
        "FFmpeg": shutil.which("ffmpeg") is not None,
        "FFprobe": shutil.which("ffprobe") is not None,
        "FFmpeg ASS/subtitles": " ass " in filters or " subtitles " in filters,
        "macOS Helvetica font": Path("/System/Library/Fonts/Helvetica.ttc").exists(),
        "OpenAI key": bool(os.environ.get("OPENAI_API_KEY")),
        "YouTube OAuth client": settings.path("youtube_client_secret").exists(),
        "YouTube token": settings.path("youtube_token").exists(),
    }
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'WAIT'}  {name}")
    essential = all(
        checks[name]
        for name in (
            "Python 3.11+",
            "FFmpeg",
            "FFprobe",
            "FFmpeg ASS/subtitles",
            "macOS Helvetica font",
        )
    )
    return 0 if essential else 1


def cost_plan(settings) -> None:
    models = settings.models
    print("One-video test models (account access still requires API-key preflight):")
    print(f"  story: local mirror fixture; fallback {models['story']}")
    print(f"  quality review: {models['reviewer']}")
    print(f"  images: 8 x {models['image']} ({models['image_quality']}, 1024x1536)")
    print(f"  speech: {models['speech']} / built-in {models['voice']} voice")
    print(f"  caption alignment: {models['alignment']}")
    print("Expected calculated cost: about $0.45 (usage dependent; not confirmed billing)")
    print("Conservative pre-request reserves: $1.69; $1.75 if fixture needs one replacement")
    print(f"Proposed hard local allowance: ${float(settings.costs['default_allowance_usd']):.2f}")
    print("The local gate cannot cap a request already accepted by the API or resolve an unknown charge.")


def main() -> None:
    args = parser().parse_args()
    load_dotenv()
    settings = load_settings(args.config)
    if args.command == "doctor":
        raise SystemExit(doctor(settings))
    if args.command == "cost-plan":
        cost_plan(settings)
        return
    if args.command == "auth-youtube":
        print(f"Saved YouTube authorization to {authorize(settings)}")
        return
    if args.command == "scheduler":
        from .scheduler import run_scheduler

        run_scheduler(settings)
        return
    if args.command == "run":
        video = Pipeline(settings).run(
            format_name=args.format,
            dry_run=args.dry_run,
            upload=args.upload,
            privacy=args.privacy,
            resume=args.resume,
            fixture=args.fixture,
            allowance_usd=args.max_cost_usd,
            paid_approved=args.approve_paid,
            story_file=args.story_file,
            review_only=args.review_only,
        )
        print(video)


if __name__ == "__main__":
    main()
