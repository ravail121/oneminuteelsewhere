"""Loopback-only, manually operated local browser dashboard."""
from __future__ import annotations

import argparse
import fcntl
import os
import secrets
import subprocess
import sys
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException
from werkzeug.serving import WSGIRequestHandler, make_server

from .cli import load_dotenv
from .config import load_settings
from .control_center import register_control_center
from .cron_jobs import register_cron_jobs
from .dashboard_store import (
    DURATIONS,
    STORY_TYPES,
    STYLES,
    VOICES,
    Conflict,
    DashboardStore,
    NewVideo,
    read_json,
)
from .languages import COUNTRIES, LANGUAGES
from .trends import TrendStore
from .youtube_dashboard import register_youtube

# Loopback-only by default (unchanged). One or more additional trusted hosts (comma-separated)
# can be set via DASHBOARD_TRUSTED_HOST for a deliberately exposed deployment that puts a
# TLS-terminating, password-authenticated reverse proxy in front of this app — never enabled by
# default, and the Flask/Werkzeug server itself still only ever binds to 127.0.0.1 (see main()).
TRUSTED_HOSTS = {h.strip() for h in os.environ.get("DASHBOARD_TRUSTED_HOST", "").split(",") if h.strip()}


def create_app(settings=None, *, store=None, control_center_synchronous=False):
    settings = settings or load_settings()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=16384,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")
    manager = store or DashboardStore(settings)
    app.extensions["dashboard_store"] = manager
    app.extensions["trend_store"] = TrendStore(settings.root)

    @app.before_request
    def local_security():
        host = request.host.split(":")[0]
        if host != "127.0.0.1" and host not in TRUSTED_HOSTS:
            abort(403, "Use the dashboard's trusted address.")
        session.setdefault("csrf", secrets.token_hex(32))
        if request.method == "POST":
            if request.headers.get("Origin") == "null":
                abort(403, "Your browser hid this form's origin. Open New Video or refresh the project page, then try again.")
            allowed_origins = {"http://" + request.host, "https://" + request.host}
            if request.headers.get("Origin") not in ({None} | allowed_origins):
                abort(403, "Cross-origin requests are not allowed.")
            if not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
                abort(403, "This form expired. Refresh the page before submitting again.")

    @app.after_request
    def headers(response):
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            # no-referrer can make native form POSTs send Origin: null, even
            # locally. Keep the origin on local forms without leaking referrers
            # to other sites; do not allow opaque origins through the guard.
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY", "Cache-Control": "no-store",
        })
        # Defense in depth: even a provider/user text accidentally containing the key
        # cannot reach HTML or JSON. Binary media never contains request credentials.
        if response.mimetype in {"text/html", "application/json"} and not response.direct_passthrough:
            for name in ("OPENAI_API_KEY", "YOUTUBE_DATA_API_KEY"):
                key = os.environ.get(name)
                if key:
                    response.set_data(response.get_data().replace(key.encode(), b"[secret removed]"))
        return response

    @app.context_processor
    def common():
        cron_jobs = app.extensions.get("cron_jobs")
        return {"csrf": session.get("csrf", ""), "story_types": STORY_TYPES, "styles": STYLES,
                "languages": LANGUAGES, "countries": COUNTRIES,
                "voices": VOICES, "durations": DURATIONS, "money": lambda v: "unknown" if v is None else f"${v:.6f}",
                "cron_active": bool(cron_jobs and cron_jobs.enabled)}

    @app.get("/")
    def new_video():
        return render_template("new.html", submission=secrets.token_hex(32), values=NewVideo().model_dump())

    def trend_results(options, submission):
        snapshot = app.extensions["trend_store"].lookup()
        return render_template("trends.html", snapshot=snapshot, submission=submission,
                               values=options.model_copy(update={"video_mode": "viral", "country": "AUTO"}).model_dump())

    @app.post("/trends")
    def discover_trends():
        values = {k: request.form[k] for k in NewVideo.model_fields if k in request.form}
        return trend_results(NewVideo.model_validate(values), secrets.token_hex(32))

    @app.post("/api/trends/recommendation")
    def recommend_trend():
        snapshot = app.extensions["trend_store"].lookup()
        # A genuinely random pick among the strongest candidates, not always the single #1 topic,
        # so repeated Viral Material videos land on different real trending subjects.
        top = secrets.choice(snapshot["records"][:5]) if snapshot["records"] else None
        return jsonify({"snapshot_id": snapshot["id"], "topic": top,
                        "language_name": LANGUAGES[top["suggested_language"]] if top else None,
                        "fetched_at": snapshot["fetched_at"], "calculated_cost_usd": 0,
                        "sources_responded": snapshot["sources_responded"],
                        "sources_attempted": len(snapshot["discovery_sources"]),
                        "unavailable_sources": snapshot["unavailable_sources"],
                        "ranking_method": snapshot["ranking_method"]})

    @app.post("/projects")
    def create_project():
        submission = request.form.get("submission", "")
        if len(submission) != 64:
            raise ValueError("Refresh the New Video form and try again.")
        values = {k: request.form[k] for k in NewVideo.model_fields if k in request.form}
        options = NewVideo.model_validate(values)
        # A real trend is optional bonus seasoning for Viral Material's locked Gaming/Football
        # niches, not a requirement — proceed straight to project creation either way.
        project_id = manager.create(options, submission)
        return redirect(url_for("confirm_story", project_id=project_id), code=303)

    @app.get("/projects/<project_id>/confirm-story")
    def confirm_story(project_id):
        project = manager.public(project_id)
        config = manager.settings(manager.load(project_id))
        return render_template("confirm.html", p=project, model=config.models["story"],
                               reserve=config.costs["request_reserves_usd"]["story"])

    @app.get("/projects/<project_id>")
    def project_page(project_id):
        return render_template("project.html", p=manager.public(project_id))

    @app.get("/projects/<project_id>/panel")
    def project_panel(project_id):
        return render_template("panel.html", p=manager.public(project_id))

    @app.get("/api/projects/<project_id>")
    def progress(project_id):
        return jsonify(manager.public(project_id))

    @app.get("/projects/<project_id>/diagnostics.json")
    def diagnostics(project_id):
        project = manager.public(project_id)
        failures = [{key: item.get(key) for key in ("stage", "revision", "model_requested", "started_at",
                    "completed_at", "calculated_cost_usd", "charge_status", "diagnostic")}
                    for item in project["requests"] if item.get("diagnostic")]
        response = jsonify({"project_id": project_id, "status": project["status"],
                            "calculated_cost_usd": project["calculated_cost"], "failures": failures,
                            "note": "Local diagnostic export. No keys, headers, narration, prompts or raw provider messages. Exporting this report makes no API requests."})
        response.headers["Content-Disposition"] = 'attachment; filename="api-diagnostics.json"'
        return response

    @app.post("/projects/<project_id>/<action>")
    def project_action(project_id, action):
        version = int(request.form.get("version", "-1"))
        if action in {"generate", "approve", "resume"}:
            if request.form.get("paid_consent") != "yes":
                raise ValueError("Confirm the paid API notice before starting this action.")
            manager.dispatch(project_id, action, version)
        elif action == "cancel":
            manager.cancel(project_id)
        elif action == "reject":
            manager.reject(project_id, version)
        elif action == "edit":
            manager.edit(project_id, version, request.form.get("title", ""), request.form.get("script", ""))
        elif action == "open-folder":
            if sys.platform != "darwin":
                raise ValueError("Opening a folder is supported only on macOS; use the displayed path.")
            directory = manager.revision_dir(manager.load(project_id))
            subprocess.run(["/usr/bin/open", str(directory)], check=True, capture_output=True)
        else:
            abort(404)
        return redirect(url_for("project_page", project_id=project_id), code=303)

    @app.get("/projects/<project_id>/draft/<int:revision>")
    def saved_story(project_id, revision):
        project = manager.load(project_id)
        directory = manager.revision_dir(project, revision)
        return render_template("saved_story.html", p=manager.public(project_id), revision=revision,
                               draft=read_json(directory / "draft.json"), review=read_json(directory / "review.json"))

    @app.get("/media/<project_id>/<int:revision>/<name>")
    def media(project_id, revision, name):
        allowed = {"final.mp4", "captions.srt", "narration.wav"} | {f"scene-{i:02d}.png" for i in range(1, 9)}
        if name not in allowed:
            abort(404)
        directory = manager.revision_dir(manager.load(project_id), revision)
        path = directory / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            abort(404)
        return send_file(path, conditional=True, as_attachment=request.args.get("download") == "1")

    @app.get("/history")
    def history():
        return render_template("history.html", rows=manager.history())

    def legacy_directory(name):
        matches = [row for row in manager.history() if row["legacy"] and row["id"] == name]
        if not matches:
            abort(404)
        path = manager.output / name
        if path.is_symlink() or not path.resolve().is_relative_to(manager.output):
            abort(404)
        return path

    @app.get("/existing/<name>")
    def legacy(name):
        directory = legacy_directory(name)
        return render_template("legacy.html", name=name, directory=directory,
            story=read_json(directory / "story.json", {}), validation=read_json(directory / "validation.json", {}),
            cost=read_json(directory / "cost-report.json", {}).get("summary", {}),
            video=(directory / "final.mp4").is_file())

    @app.get("/existing/<name>/video")
    def legacy_video(name):
        directory = legacy_directory(name)
        path = directory / "final.mp4"
        if not path.is_file() or path.is_symlink():
            abort(404)
        return send_file(path, conditional=True)

    @app.errorhandler(Exception)
    def friendly_error(error):
        # Never log raw exception strings, request bodies, or SDK payloads.
        if isinstance(error, HTTPException):
            status, message = error.code, error.description
        elif isinstance(error, FileNotFoundError):
            status, message = 404, "That saved project or file was not found."
        elif isinstance(error, ValidationError):
            status, message = 400, "Check the form choices, text lengths, and spending limit ($0.05–$10)."
        elif isinstance(error, ValueError):
            status, message = (409 if isinstance(error, Conflict) else 400), str(error)
        else:
            status, message = 400, "The action stopped safely. Check saved progress and costs; no automatic retry was made."
        return render_template("error.html", message=message), status

    register_youtube(app, settings, manager)
    center = register_control_center(app, settings, manager, app.extensions["youtube_dashboard"],
                            trend_store=app.extensions["trend_store"], synchronous=control_center_synchronous)
    register_cron_jobs(app, settings, center)
    return app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass  # URLs/query strings can contain private user text.

    def log_error(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Start the local, manually operated Shorts dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535")
    settings = load_settings(args.config)
    load_dotenv(settings.root / ".env")
    full_ffmpeg = Path("/opt/homebrew/opt/ffmpeg-full/bin")
    if full_ffmpeg.exists():
        os.environ["PATH"] = str(full_ffmpeg) + os.pathsep + os.environ.get("PATH", "")
    lock_dir = settings.root / "data"
    lock_dir.mkdir(exist_ok=True)
    with (lock_dir / "dashboard.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "A dashboard is already running for this project. Use its browser window.\n")
        app = create_app(settings)
        server = make_server("127.0.0.1", args.port, app, threaded=True, request_handler=QuietRequests)
        address = f"http://127.0.0.1:{args.port}"
        print(f"Local dashboard: {address}\nNo API calls occur until you confirm an action. Ctrl+C stops the dashboard.", flush=True)
        cron_jobs = app.extensions["cron_jobs"]
        cron_jobs.start_scheduler()
        print(f"Cron jobs {'enabled' if cron_jobs.enabled else 'paused'} — see /cron-jobs for the schedule and run history.", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopping. Any accepted request will be saved; no next request will start.", flush=True)
        finally:
            cron_jobs.shutdown()
            app.extensions["dashboard_store"].shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
