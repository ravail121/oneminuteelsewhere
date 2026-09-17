"""Offline cron job tests. Reuses the same mocked Control Center fixture as its own tests;
never starts a real background scheduler and never makes a real request."""
import copy
from datetime import datetime
from pathlib import Path

from test_control_center import cc, connect  # noqa: F401 - shared mocked fixture/helper

from elsewhere.config import Settings, load_settings
from elsewhere.control_center import ControlCenter, ControlCenterError
from elsewhere.cron_jobs import SCHEDULE, CronJobs
from elsewhere.dashboard_store import DashboardStore
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
    project = cc.manager.load(row["project"]["id"])
    assert project["video_mode"] == SCHEDULE[0][1]


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
