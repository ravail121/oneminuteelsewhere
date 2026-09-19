"""Offline cron job tests. Reuses the same mocked Control Center fixture as its own tests;
never starts a real background scheduler and never makes a real request."""
import copy
from datetime import datetime, timedelta
from pathlib import Path

from test_control_center import cc, connect  # noqa: F401 - shared mocked fixture/helper

from elsewhere.config import Settings, load_settings
from elsewhere.control_center import ControlCenter, ControlCenterError
from elsewhere.cron_jobs import SCHEDULE, CronJobs
from elsewhere.dashboard_store import DashboardStore, read_json
from elsewhere.openai_service import save_json
from elsewhere.youtube_dashboard import YouTubeDashboard

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def cron_of(cc):  # noqa: F811 - fixture shadow is deliberate, matches test_control_center's own style
    return cc.app.extensions["cron_jobs"]


def test_schedule_is_five_cast_and_five_viral():
    assert len(SCHEDULE) == 10
    modes = [mode for _, mode in SCHEDULE]
    assert modes.count("messi_ronaldo") == 5
    assert modes.count("viral") == 5
    times = [time for time, _ in SCHEDULE]
    assert times == sorted(times)  # ascending, easy to read on the page


def test_fire_starts_the_same_control_center_flow_as_a_manual_click(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    cron.fire(0)
    rows = cron.slot_rows()
    row = rows[0]
    assert row["video_mode"] == SCHEDULE[0][1]
    assert row["project"] is not None
    assert row["status"] == "complete"
    assert row["project"]["link"] == f"/projects/{row['project']['id']}"
    assert row["cost"] > 0
    assert row["progress"] == 100
    project = cc.manager.load(row["project"]["id"])
    assert project["video_mode"] == SCHEDULE[0][1]


def test_manual_run_bypasses_pause_and_always_starts_a_fresh_project(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(False)  # paused — a scheduled fire would be skipped
    cron.fire(4, manual=True)
    first = cron.slot_rows()[4]
    assert first["project"] is not None
    assert first["manual"] is True
    cron.fire(4, manual=True)  # a second manual click must not be deduped against the first
    second = cron.slot_rows()[4]
    assert second["project"]["id"] != first["project"]["id"]
    assert len(list(cc.manager.output.glob("dashboard-*"))) == 2


def test_today_total_cost_sums_only_todays_fired_slots(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    cron.fire(0)
    cron.fire(1)
    rows = cron.slot_rows()
    expected = round((rows[0]["cost"] or 0) + (rows[1]["cost"] or 0), 6)
    assert expected > 0
    assert cron.today_total_cost(rows) == expected


def test_run_now_route_starts_a_run_and_redirects(cc):  # noqa: F811
    connect(cc)
    cc.browser.get("/cron-jobs", base_url=BASE)
    with cc.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    response = cc.browser.post("/cron-jobs/run/5", base_url=BASE, data={"csrf": csrf})
    assert response.status_code == 303
    row = cron_of(cc).slot_rows()[5]
    assert row["project"] is not None
    assert row["manual"] is True


def test_fire_is_idempotent_for_the_same_day_and_slot(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    cron.fire(1)
    first = cron.slot_rows()[1]["project"]["id"]
    cron.fire(1)  # a misfire replay or app restart firing the same slot again the same day
    second = cron.slot_rows()[1]["project"]["id"]
    assert first == second
    assert len(list(cc.manager.output.glob("dashboard-*"))) == 1


def test_run_cleanup_removes_old_finished_project_output(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    directory = cc.manager.output / "dashboard-old123"
    directory.mkdir(parents=True)
    old_created_at = (datetime.now(cron._tz()) - timedelta(days=30)).isoformat()
    save_json(directory / "dashboard-project.json",
             {"id": "old123", "status": "complete", "busy": False, "created_at": old_created_at})
    cron.run_cleanup()
    assert not directory.exists()


def test_a_previous_days_run_shows_as_scheduled_not_carried_over(cc):  # noqa: F811
    # Once a day rolls over, a slot that already ran yesterday must look freshly scheduled for
    # today, not still "complete" with yesterday's cost/project attached as if that already
    # happened today.
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    cron.fire(6)
    today_str = datetime.now(cron._tz()).strftime("%Y-%m-%d")
    today_path = cron._run_path(today_str, 6)
    record = read_json(today_path)
    yesterday = datetime.now(cron._tz()) - timedelta(days=1)
    record["fired_at"] = yesterday.isoformat()
    save_json(cron._run_path(yesterday.strftime("%Y-%m-%d"), 6), record)
    today_path.unlink()
    row = cron.slot_rows()[6]
    assert row["status"] == "scheduled"
    assert row["cost"] is None and row["progress"] is None and row["message"] is None
    assert row["project"] is None
    assert row["last_fired_at"].date() == yesterday.date()


def test_paused_cron_jobs_skip_without_starting_anything(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(False)
    cron.fire(2)
    row = cron.slot_rows()[2]
    assert row["project"] is None
    assert row["status"] == "error"
    assert "paused" in row["message"]
    assert cc.manager.history() == []


def test_fire_records_a_control_center_error_without_leaking_provider_details(cc, monkeypatch):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    def blocked(*args, **kwargs):
        raise ControlCenterError("Monthly video limit reached (200 completed this month). No new project was started.")
    monkeypatch.setattr(cc.control_center, "start", blocked)
    cron.fire(3)
    row = cron.slot_rows()[3]
    assert row["project"] is None
    assert row["status"] == "error"
    assert "Monthly video limit reached" in row["message"]


def test_fire_logs_and_survives_if_even_the_failure_record_cannot_be_saved(cc, monkeypatch, caplog):  # noqa: F811
    # Reproduces what actually happened when the server's disk filled up: fire() hit an
    # error, then its own _record() call to save that error ALSO failed (no space left),
    # which used to propagate uncaught with zero trace — not even the warning log that
    # would otherwise explain the failure, since it ran after the now-failing write.
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    def blocked(*args, **kwargs):
        raise ControlCenterError("Monthly video limit reached (200 completed this month). No new project was started.")
    monkeypatch.setattr(cc.control_center, "start", blocked)
    def flaky_record(*args, **kwargs):
        raise OSError("No space left on device")
    monkeypatch.setattr(cron, "_record", flaky_record)
    with caplog.at_level("WARNING", logger="elsewhere.cron_jobs"):
        cron.fire(3)  # must not raise even though recording the failure also fails
    assert "Monthly video limit reached" in caplog.text
    assert "could not save the failure record either" in caplog.text


def test_next_fire_time_is_always_in_the_future(tmp_path):
    settings = Settings(tmp_path, copy.deepcopy(load_settings(ROOT / "config.yaml").raw))
    store = DashboardStore(settings, synchronous=True)
    studio = YouTubeDashboard(settings, store)
    center = ControlCenter(settings, store, studio, synchronous=True)
    cron = CronJobs(settings, center)
    now = datetime.now(cron._tz())
    for index in range(len(SCHEDULE)):
        assert cron.next_fire_time(index) > now


def test_cron_jobs_page_and_toggle_route(cc):  # noqa: F811
    connect(cc)
    page = cc.browser.get("/cron-jobs", base_url=BASE)
    assert page.status_code == 200
    assert b"Cron Jobs" in page.data
    with cc.browser.session_transaction(base_url=BASE) as session:
        csrf = session["csrf"]
    off = cc.browser.post("/cron-jobs/toggle", base_url=BASE, data={"csrf": csrf, "enabled": "no"})
    assert off.status_code == 303
    assert cron_of(cc).enabled is False
    with cc.browser.session_transaction(base_url=BASE) as session:
        csrf2 = session["csrf"]
    on = cc.browser.post("/cron-jobs/toggle", base_url=BASE, data={"csrf": csrf2, "enabled": "yes"})
    assert on.status_code == 303
    assert cron_of(cc).enabled is True


def test_base_page_footer_reflects_cron_state(cc):  # noqa: F811
    connect(cc)
    cron = cron_of(cc)
    cron.set_enabled(True)
    page = cc.browser.get("/", base_url=BASE)
    assert b"posts automatically" in page.data
    cron.set_enabled(False)
    page = cc.browser.get("/", base_url=BASE)
    assert b"Nothing is uploaded automatically" in page.data
