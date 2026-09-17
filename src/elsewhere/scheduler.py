from __future__ import annotations

import logging
import os

from .config import Settings
from .pipeline import Pipeline

LOG = logging.getLogger("elsewhere.scheduler")


def _run(settings: Settings, format_name: str) -> None:
    public = os.environ.get("AUTOPUBLISH", "false").lower() == "true"
    privacy = "public" if public else "private"
    try:
        video = Pipeline(settings).run(format_name, upload=True, privacy=privacy)
        LOG.info("Completed %s production: %s", format_name, video)
    except Exception:
        LOG.exception("Scheduled %s production failed", format_name)


def run_scheduler(settings: Settings) -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:
        raise RuntimeError("Install project dependencies before running the scheduler") from exc
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    scheduler = BlockingScheduler(timezone=settings.brand["timezone"])
    for index, value in enumerate(settings.publishing["short_times_local"], 1):
        hour, minute = (int(part) for part in value.split(":"))
        scheduler.add_job(
            _run,
            "cron",
            args=[settings, "short"],
            hour=hour,
            minute=minute,
            id=f"short-{index}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    long_hour, long_minute = (
        int(part) for part in settings.publishing["long_time_local"].split(":")
    )
    scheduler.add_job(
        _run,
        "cron",
        args=[settings, "long"],
        day_of_week=settings.publishing["long_weekday"][:3].lower(),
        hour=long_hour,
        minute=long_minute,
        id="long-weekly",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=7200,
    )
    mode = "PUBLIC" if os.environ.get("AUTOPUBLISH", "false").lower() == "true" else "PRIVATE TEST"
    LOG.info("Scheduler started in %s mode using %s", mode, settings.brand["timezone"])
    scheduler.start()
