import json
import logging
import sys
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request

from ymd import core
from ymd.downloader import DownloadOptions, DownloadProgress, DownloadState, Downloader
from ymd.settings import Profile, SettingsManager

app = Flask(__name__, template_folder=Path(__file__).parent / "templates")

logger = logging.getLogger("yandex-music-downloader-web")

settings = SettingsManager()

# Active download jobs: job_id -> {"downloader": Downloader, "thread": Thread, "events": list}
_jobs: dict[str, dict] = {}


@app.route("/")
def index():
    profiles = settings.list_profiles()
    active_profile = settings.get_active_profile()
    return render_template(
        "index.html",
        profiles=profiles,
        active_profile=active_profile,
    )


@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    names = settings.list_profiles()
    profiles = {name: settings.get_profile(name).__dict__ for name in names}
    return jsonify({"profiles": profiles, "active": settings.get_active_profile_name()})


@app.route("/api/profiles/<name>", methods=["GET"])
def get_profile(name: str):
    profile = settings.get_profile(name)
    return jsonify(profile.__dict__)


@app.route("/api/profiles", methods=["POST"])
def save_profile():
    data = request.json
    profile = Profile(**data)
    settings.save_profile(profile)
    return jsonify({"ok": True})


@app.route("/api/profiles/<name>", methods=["DELETE"])
def delete_profile(name: str):
    settings.delete_profile(name)
    return jsonify({"ok": True})


@app.route("/api/profiles/active", methods=["POST"])
def set_active_profile():
    data = request.json
    settings.set_active_profile(data["name"])
    return jsonify({"ok": True})


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    token = data.get("token", "")
    if not token:
        return jsonify({"error": "No token provided"}), 400

    lyrics_map = {
        "none": core.LyricsFormat.NONE,
        "text": core.LyricsFormat.TEXT,
        "lrc": core.LyricsFormat.LRC,
    }

    options = DownloadOptions(
        token=token,
        quality=data.get("quality", 0),
        output_dir=Path(data.get("output_dir", ".")),
        path_pattern=Path(data.get("path_pattern", str(core.DEFAULT_PATH_PATTERN))),
        skip_existing=data.get("skip_existing", True),
        embed_cover=data.get("embed_cover", False),
        lyrics_format=lyrics_map.get(data.get("lyrics_format", "none"), core.LyricsFormat.NONE),
        delay=data.get("delay", 0),
        stick_to_artist=data.get("stick_to_artist", False),
        only_music=data.get("only_music", False),
        compatibility_level=data.get("compatibility_level", 1),
        timeout=data.get("timeout", 20),
        tries=data.get("tries", 20),
        retry_delay=data.get("retry_delay", 5),
    )

    job_id = str(uuid.uuid4())[:8]
    downloader = Downloader(options)
    events: list[dict] = []

    def on_progress(progress: DownloadProgress):
        events.append(
            {
                "current": progress.current,
                "total": progress.total,
                "track_title": progress.track_title,
                "track_artist": progress.track_artist,
                "status": progress.status,
                "file_path": progress.file_path,
                "codec": progress.codec,
                "bitrate": progress.bitrate,
                "state": progress.state.name,
                "errors": progress.errors,
                "queue_remaining": progress.queue_remaining,
            }
        )

    def run_download():
        downloader.download(urls, on_progress)
        events.append({"status": "finished", "state": "IDLE", "errors": downloader.errors})

    thread = threading.Thread(target=run_download, daemon=True)
    _jobs[job_id] = {"downloader": downloader, "thread": thread, "events": events}
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def progress_stream(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        sent = 0
        while True:
            events = job["events"]
            while sent < len(events):
                event = events[sent]
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                sent += 1
                if event.get("status") == "finished":
                    return
            # Check if thread is still alive
            if not job["thread"].is_alive() and sent >= len(events):
                yield f"data: {json.dumps({'status': 'finished', 'state': 'IDLE', 'errors': 0})}\n\n"
                return
            import time

            time.sleep(0.2)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/pause/<job_id>", methods=["POST"])
def pause_download(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job["downloader"].pause()
    return jsonify({"ok": True, "state": "PAUSED"})


@app.route("/api/resume/<job_id>", methods=["POST"])
def resume_download(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job["downloader"].resume()
    return jsonify({"ok": True, "state": "DOWNLOADING"})


@app.route("/api/stop/<job_id>", methods=["POST"])
def stop_download(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job["downloader"].stop()
    return jsonify({"ok": True, "state": "STOPPING"})


def main():
    port = 5000
    print(f"Запуск веб-интерфейса на http://localhost:{port}")
    print("Откройте эту ссылку в браузере, если она не открылась автоматически.")
    webbrowser.open(f"http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
