"""Read a public Apple Music playlist with no login.

Mirror of ``spotify.py`` for the reverse direction. Apple's public playlist page
ships the full track list inside a ``serialized-server-data`` blob — the same data
the web player renders. No account, no token, no API key.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .spotify import Track  # shared track shape (title / artist / duration_ms)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
_SSD_RE = re.compile(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', re.S)
_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_OG_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"')
_URL_RE = re.compile(r'https?://music\.apple\.com/[a-z]{2}/playlist/[^\s"\']+', re.I)
_PLID_RE = re.compile(r'pl\.[A-Za-z0-9]+')


class AppleReadError(Exception):
    """The playlist could not be read. Message is safe to show the user."""


def valid_url(url_or: str) -> str:
    """Return the Apple Music playlist URL to fetch, or raise."""
    s = (url_or or "").strip()
    m = _URL_RE.search(s)
    if not m or not _PLID_RE.search(s):
        raise AppleReadError(
            "That doesn't look like an Apple Music playlist link. Paste something "
            "like https://music.apple.com/us/playlist/name/pl.xxxxxxxx"
        )
    return m.group(0)


def _playlist_name(html: str) -> str:
    m = _LD_RE.search(html)
    if m:
        try:
            n = json.loads(m.group(1)).get("name")
            if n:
                return n
        except ValueError:
            pass
    m = _OG_RE.search(html)
    return (m.group(1) if m else "Apple Music Playlist")


def _collect_tracks(node, out: list) -> None:
    # Track rows carry an id like "track-lockup - pl.xxx - <songId>" plus a title
    # and artistName — the reliable signal across playlist layouts.
    if isinstance(node, dict):
        if str(node.get("id", "")).startswith("track-lockup") and node.get("title") and "artistName" in node:
            out.append(node)
        for v in node.values():
            _collect_tracks(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_tracks(v, out)


def read_playlist(url_or: str, *, timeout: int = 20):
    """Return ``(playlist_name, [Track, ...], truncated)`` for a public Apple playlist."""
    page = valid_url(url_or)
    req = urllib.request.Request(page, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise AppleReadError(
                "That playlist wasn't found. Check the link and make sure it's public."
            ) from e
        raise AppleReadError(f"Apple Music returned an error ({e.code}).") from e
    except Exception as e:
        raise AppleReadError(f"Couldn't reach Apple Music ({e}).") from e

    name, tracks, truncated = parse_page(html)
    if not tracks:
        raise AppleReadError(
            "No tracks found (the playlist may be private, empty, or in a format "
            "we can't read — try the CSV path with TuneMyMusic)."
        )
    return name, tracks, truncated


def parse_page(html: str):
    """Pure parse of a fetched Apple playlist page — split out so it's testable."""
    m = _SSD_RE.search(html)
    if not m:
        raise AppleReadError(
            "Apple didn't return track data (the playlist may be private, or the "
            "page format changed)."
        )
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise AppleReadError("Couldn't parse Apple Music's response.") from e

    raw: list = []
    _collect_tracks(data, raw)
    tracks = []
    for it in raw:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        dur = it.get("duration")
        tracks.append(
            Track(
                title=title,
                artist=(it.get("artistName") or "").strip(),
                duration_ms=dur if isinstance(dur, int) else None,
                explicit=bool(it.get("showExplicitBadge")),
            )
        )
    return _playlist_name(html), tracks, False
