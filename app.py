#!/usr/bin/env python3
"""spot2am — a tiny local web app.

Paste a public Spotify playlist link → get a CSV (always) and, if you've saved
your Apple Music tokens, a one-click push straight into your library.

Run:  python3 app.py     (or ./run.sh)   then open the printed URL.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request

from spot2am import apple_read, applemusic, csvout, matcher, spotify_write
from spot2am.apple_read import AppleReadError
from spot2am.spotify import SpotifyReadError, read_playlist

# Direction keys: "s2a" = Spotify -> Apple Music, "a2s" = Apple Music -> Spotify.

BASE = Path(__file__).resolve().parent
EXPORTS = BASE / "exports"
CONFIG_PATH = BASE / "config.json"
EXPORTS.mkdir(exist_ok=True)

app = Flask(__name__)

# In-memory record of the last conversions, so Push can reuse matched ids
# without re-hitting Spotify/iTunes. Fine for a single-user local tool.
JOBS: dict[str, dict] = {}

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@app.before_request
def _local_only_guard():
    """This app holds your tokens and can create playlists, so lock it to the
    loopback UI. The Host check (every request, incl. GET /stream) blocks
    DNS-rebinding; the Origin check on writes blocks cross-site POSTs (CSRF).
    """
    host = (request.headers.get("Host") or "").rsplit(":", 1)[0]
    if host not in _LOCAL_HOSTS:
        return jsonify(ok=False, error="Blocked: non-local host."), 403
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and (urlparse(origin).hostname or "") not in _LOCAL_HOSTS:
            return jsonify(ok=False, error="Blocked: cross-origin request."), 403
    return None


# --------------------------------------------------------------------------- #
# config (Apple tokens + storefront) — stored locally, gitignored
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except ValueError:
            pass
    return {"bearer": "", "media_user_token": "", "country": "us", "spotify_token": ""}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:  # tokens are sensitive — keep the file owner-only (rw-------)
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def apple_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("bearer") and cfg.get("media_user_token"))


def spotify_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("spotify_token"))


def can_push(direction: str, cfg: dict) -> bool:
    return spotify_ready(cfg) if direction == "a2s" else apple_ready(cfg)


def build_searchers(cfg: dict, direction: str):
    """Ordered song-search sources for the matcher, per direction.

    Spotify -> Apple: the real Apple catalog first (when tokens are set), then the
    always-available iTunes index. Apple -> Spotify: the Spotify catalog, which
    needs a token — so with no token there is nothing to match against (the CSV
    still works via TuneMyMusic)."""
    if direction == "a2s":
        return [spotify_write.searcher(cfg["spotify_token"])] if spotify_ready(cfg) else []
    country = cfg.get("country", "us")
    searchers = []
    if apple_ready(cfg):
        searchers.append(
            applemusic.catalog_searcher(country, cfg["bearer"], cfg["media_user_token"])
        )
    searchers.append(matcher.itunes_searcher(country))
    if country != "us":
        searchers.append(matcher.itunes_searcher("us"))
    return searchers


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    cfg = load_config()
    return render_template(
        "index.html",
        apple_ready=apple_ready(cfg),
        spotify_ready=spotify_ready(cfg),
        country=cfg.get("country", "us"),
    )


@app.post("/convert")
def convert():
    """Fast phase: read the source playlist and return the track list right away,
    so the UI renders every row in ~1s. Matching is streamed after, via /stream."""
    body = request.json or {}
    url = body.get("url", "")
    direction = "a2s" if body.get("direction") == "a2s" else "s2a"
    try:
        if direction == "a2s":
            name, tracks, truncated = apple_read.read_playlist(url)
        else:
            name, tracks, truncated = read_playlist(url)
    except (SpotifyReadError, AppleReadError) as e:
        return jsonify(ok=False, error=str(e)), 400

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"name": name, "tracks": tracks, "direction": direction, "ids": [], "csv": None}
    cfg = load_config()
    return jsonify(
        ok=True,
        job_id=job_id,
        name=name,
        direction=direction,
        truncated=truncated,
        count=len(tracks),
        can_match=bool(build_searchers(cfg, direction)),
        tracks=[{"title": t.title, "artist": t.artist} for t in tracks],
    )


@app.get("/stream/<job_id>")
def stream(job_id):
    """Slow phase: match each track and stream the result as it resolves (SSE),
    then write the CSV. The row fills in live instead of blocking on the whole set."""
    job = JOBS.get(job_id)
    if not job:
        return Response(
            'data: {"type": "error", "error": "That run expired \\u2014 convert again."}\n\n',
            mimetype="text/event-stream",
        )
    cfg = load_config()
    direction = job.get("direction", "s2a")
    searchers = build_searchers(cfg, direction)

    def events():
        rows = []
        for i, track in enumerate(job["tracks"]):
            m = matcher.match_track(track, searchers) if searchers else matcher.NO_MATCH
            rows.append((track, m))
            yield "data: " + json.dumps(
                {
                    "type": "match",
                    "i": i,
                    "matched": m.matched,
                    "apple_name": m.apple_name,
                    "apple_artist": m.apple_artist,
                }
            ) + "\n\n"
            if searchers:
                time.sleep(0.06)  # gentle spacing so the search API doesn't throttle

        fname = f"{csvout.safe_filename(job['name'])}-{job_id}.csv"
        (EXPORTS / fname).write_text(csvout.to_csv(rows), encoding="utf-8")
        matched = [(t, m) for t, m in rows if m.matched]
        job["ids"] = [m.apple_id for _, m in matched]
        job["csv"] = fname
        yield "data: " + json.dumps(
            {
                "type": "done",
                "matched": len(matched),
                "total": len(rows),
                "csv_url": f"/download/{fname}",
                "can_push": can_push(direction, cfg),
                "matched_ran": bool(searchers),
            }
        ) + "\n\n"

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/download/<path:fname>")
def download(fname: str):
    safe = os.path.basename(fname)
    path = EXPORTS / safe
    if not path.exists():
        return "Not found", 404
    return Response(
        path.read_bytes(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@app.post("/push")
def push():
    job_id = (request.json or {}).get("job_id", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify(ok=False, error="That conversion expired — run it again."), 400
    if not job["ids"]:
        return jsonify(ok=False, error="No matched songs to add."), 400

    cfg = load_config()
    try:
        if job.get("direction") == "a2s":
            r = spotify_write.create_playlist(
                name=job["name"],
                description="Imported from Apple Music by spot2am",
                uris=job["ids"],
                token=cfg["spotify_token"],
            )
            failed = len(r.failed)
        else:
            r = applemusic.create_playlist(
                name=job["name"],
                description="Imported from Spotify by spot2am",
                apple_ids=job["ids"],
                bearer=cfg["bearer"],
                user_token=cfg["media_user_token"],
            )
            failed = len(r.failed_ids)
    except (applemusic.AppleAuthError, applemusic.AppleApiError,
            spotify_write.SpotifyAuthError, spotify_write.SpotifyApiError) as e:
        return jsonify(ok=False, error=str(e)), 400

    return jsonify(ok=True, added=r.added, failed=failed, playlist_url=r.playlist_url)


@app.post("/settings")
def settings():
    body = request.json or {}
    cfg = load_config()
    cfg["bearer"] = body.get("bearer", cfg.get("bearer", "")).strip()
    cfg["media_user_token"] = body.get("media_user_token", cfg.get("media_user_token", "")).strip()
    cfg["spotify_token"] = body.get("spotify_token", cfg.get("spotify_token", "")).strip()
    cfg["country"] = (body.get("country") or cfg.get("country") or "us").strip().lower()
    save_config(cfg)

    detected = None
    if apple_ready(cfg):
        detected = applemusic.get_storefront(cfg["bearer"], cfg["media_user_token"])
        if detected:
            cfg["country"] = detected
            save_config(cfg)
    return jsonify(
        ok=True,
        apple_ready=apple_ready(cfg),
        spotify_ready=spotify_ready(cfg),
        country=cfg["country"],
        detected=detected,
    )


def _open_browser(url: str) -> None:
    time.sleep(0.8)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  spot2am is running →  {url}\n  (paste a Spotify playlist link there; Ctrl+C to stop)\n")
    if os.environ.get("SPOT2AM_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
