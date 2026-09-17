"""Fully automated daily posting schedule.

Creates NO new generation/upload logic of its own: each scheduled slot just calls the same
`ControlCenter.start()` a manual "Generate, Create and Upload" click already calls, at a fixed
local time instead of a click. Every existing safety net still applies exactly as it does for
a manual one-click run: monthly video/spend hard stops, the uncertain-charge block, and the
stop-before-approval warning checks. A slot that fails still means nothing was hidden — the
project and its saved costs remain, and the failure is logged here for the Cron Jobs page.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request

from .control_center import ControlCenter, ControlCenterError
from .costs import utc_now
from .dashboard_store import read_json
from .openai_service import save_json

LOG = logging.getLogger("elsewhere.cron_jobs")

# 5 Recurring Cast + 5 Viral Material slots per day, spread across ~24 hours in the channel's
# own configured timezone rather than clustered together: individual Shorts compete with each
# other for first-hour view velocity, and spreading posts catches more regional audiences
# across a full day. A reasonable starting point, not a guarantee — once real view data comes
# in from the channel's own audience, these times are worth revisiting.
SCHEDULE = [
    ("01:30", "messi_ronaldo"),
    ("03:30", "viral"),
    ("06:00", "messi_ronaldo"),
    ("08:30", "viral"),
    ("11:00", "messi_ronaldo"),
    ("13:30", "viral"),
    ("16:00", "messi_ronaldo"),
    ("18:30", "viral"),
    ("21:00", "messi_ronaldo"),
    ("23:30", "viral"),
]
RUN_HISTORY_DAYS = 14  # how far back the Cron Jobs page looks for each slot's last outcome


class CronJobs:
    def __init__(self, settings, control_center: ControlCenter):
        self.settings = settings
        self.control_center = control_center
        self.directory = settings.root / "data/cron-jobs"
        if self.directory.is_symlink():
            raise ValueError("Unsafe cron job state directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "runs").mkdir(exist_ok=True)
        self.scheduler = None
        state = read_json(self._state_path(), {})
        # Defaults to paused: this fires real paid requests and real uploads on its own, on a
        # schedule nobody has reviewed yet on first install. Turning it on is a deliberate,
        # visible click on the Cron Jobs page, the same way every other spend/upload action
        # in this app requires an explicit confirmation before anything real happens.
        self.enabled = state.get("enabled", False)

    def _state_path(self):
        return self.directory / "state.json"

    def set_enabled(self, value: bool):
        self.enabled = bool(value)
        save_json(self._state_path(), {"enabled": self.enabled})

    def _tz(self):
        from zoneinfo import ZoneInfo
        return ZoneInfo(self.settings.brand["timezone"])

    def _run_path(self, date_str, index):
        return self.directory / "runs" / f"{date_str}-slot-{index:02d}.json"

    def _record(self, date_str, index, **fields):
        path = self._run_path(date_str, index)
        record = read_json(path, {})
        record.update(fields, updated_at=utc_now())
        save_json(path, record)
        return record

    # ---- firing a slot (called by the scheduler, a manual "Run now" click, or a dry run) --
    def fire(self, index, *, manual=False):
        """Idempotent for a scheduled fire: a duplicate fire for the same local day/slot (a
        misfire replay, an app restart near the fire time) is a safe no-op via the exact same
        submission-based deduplication Control Center already uses for a manual click — it
        never starts a second project for a slot that already has one. A manual=True "Run
        now" click always starts a genuinely new run instead, using a one-off submission, and
        also ignores the paused flag — a deliberate click is its own explicit confirmation."""
        time_str, video_mode = SCHEDULE[index]
        date_str = datetime.now(self._tz()).strftime("%Y-%m-%d")
        fired_at = utc_now()
        if not self.enabled and not manual:
            self._record(date_str, index, time=time_str, video_mode=video_mode, fired_at=fired_at,
                         project_id=None, error="Cron jobs are paused; this slot was skipped.")
            LOG.info("Slot %s (%s) skipped: cron jobs paused", index, video_mode)
            return
        submission = secrets.token_hex(32) if manual else hashlib.sha256(f"cron-{date_str}-{index}".encode()).hexdigest()
        try:
            project_id = self.control_center.start(submission, privacy="public", video_mode=video_mode)
            self._record(date_str, index, time=time_str, video_mode=video_mode, fired_at=fired_at,
                         project_id=project_id, error=None, manual=manual)
            LOG.info("Slot %s (%s) started project %s%s", index, video_mode, project_id, " (manual)" if manual else "")
        except Exception as error:  # noqa: BLE001 - never leak provider payloads into the saved record
            message = str(error) if isinstance(error, ControlCenterError) else \
                f"Stopped safely: {type(error).__name__}: {str(error)[:300]}"
            self._record(date_str, index, time=time_str, video_mode=video_mode, fired_at=fired_at,
                         project_id=None, error=message, manual=manual)
            LOG.warning("Slot %s (%s) did not start: %s", index, video_mode, message)

    # ---- the real scheduler (production only; never auto-started under test) -------------
    def start_scheduler(self):
        if self.scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone=self._tz())
        for index, (time_str, _video_mode) in enumerate(SCHEDULE):
            hour, minute = (int(part) for part in time_str.split(":"))
            scheduler.add_job(self.fire, "cron", args=[index], hour=hour, minute=minute,
                              id=f"cron-slot-{index}", max_instances=1, coalesce=True, misfire_grace_time=3600)
        scheduler.start()
        self.scheduler = scheduler
        LOG.info("Cron job scheduler started (%d slots, %s, enabled=%s)",
                 len(SCHEDULE), self.settings.brand["timezone"], self.enabled)

    def shutdown(self):
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    # ---- display data for the Cron Jobs page ----------------------------------------------
    def _recent_records(self, index):
        today = datetime.now(self._tz()).date()
        records = []
        for offset in range(RUN_HISTORY_DAYS):
            date_str = (today - timedelta(days=offset)).isoformat()
            record = read_json(self._run_path(date_str, index))
            if record:
                records.append(record)
        return records

    def next_fire_time(self, index):
        time_str, _video_mode = SCHEDULE[index]
        hour, minute = (int(part) for part in time_str.split(":"))
        now = datetime.now(self._tz())
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def slot_rows(self):
        today = datetime.now(self._tz()).date()
        rows = []
        for index, (time_str, video_mode) in enumerate(SCHEDULE):
            recent = self._recent_records(index)
            last = recent[0] if recent else None
            last_fired_at = None
            if last and last.get("fired_at"):
                last_fired_at = datetime.fromisoformat(last["fired_at"]).astimezone(self._tz())
            fired_today = last_fired_at is not None and last_fired_at.date() == today
            status, message, project, cost, progress = "never run", None, None, None, None
            if last and not fired_today:
                # A day rolled over since this slot last fired: today's occurrence hasn't run
                # yet, so show it as freshly scheduled rather than carrying yesterday's
                # complete/failed status, cost and project forward as if it already happened.
                status = "scheduled"
            elif last:
                if last.get("error"):
                    status, message = "error", last["error"]
                elif last.get("project_id"):
                    try:
                        run = self.control_center.public_run(last["project_id"])
                        status, message = run["phase"], run["message"]
                        cost = run["project"]["calculated_cost"]
                        progress = run["project"]["progress"]
                    except ControlCenterError:
                        status, message = "unknown", "The linked project could no longer be found."
                    # The project's own detail page (story, scene-by-scene stage progress,
                    # images as they land) — the same page a manually created project uses.
                    project = {"id": last["project_id"], "link": f"/projects/{last['project_id']}"}
            rows.append({"index": index, "time": time_str, "video_mode": video_mode,
                        "next_fire_time": self.next_fire_time(index), "last_fired_at": last_fired_at,
                        "status": status, "message": message, "project": project,
                        "cost": cost, "progress": progress, "manual": bool(last and last.get("manual"))})
        return rows

    def today_total_cost(self, rows=None):
        rows = rows if rows is not None else self.slot_rows()
        today_str = datetime.now(self._tz()).strftime("%Y-%m-%d")
        total = 0.0
        for row in rows:
            fired_local = row.get("last_fired_at")
            if fired_local and fired_local.strftime("%Y-%m-%d") == today_str:
                total += row.get("cost") or 0
        return round(total, 6)


def register_cron_jobs(app, settings, control_center):
    cron = CronJobs(settings, control_center)
    app.extensions["cron_jobs"] = cron
    routes = Blueprint("cron_jobs", __name__)

    @routes.get("/cron-jobs")
    def home():
        rows = cron.slot_rows()
        return render_template("cron_jobs.html", rows=rows, enabled=cron.enabled,
                               total_cost_today=cron.today_total_cost(rows),
                               timezone=settings.brand["timezone"], nonce=request.args.get("nonce", ""))

    @routes.post("/cron-jobs/toggle")
    def toggle():
        cron.set_enabled(request.form.get("enabled") == "yes")
        return redirect("/cron-jobs", code=303)

    @routes.post("/cron-jobs/run/<int:index>")
    def run_now(index):
        if not 0 <= index < len(SCHEDULE):
            return redirect("/cron-jobs", code=303)
        cron.fire(index, manual=True)
        return redirect("/cron-jobs", code=303)

    app.register_blueprint(routes)
    return cron
