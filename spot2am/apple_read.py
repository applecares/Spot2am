"""Read a public Apple Music playlist, album, or song with no login.

Mirror of ``spotify.py`` for the reverse direction. Apple's public playlist and
album pages ship the full track list inside a ``serialized-server-data`` blob —
the same data the web player renders. Song pages carry the track in a JSON-LD
``MusicRecording`` node instead. No account, no token, no API key.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

from .spotify import Track  # shared track shape (title / artist / duration_ms)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
_SSD_RE = re.compile(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', re.S)
_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_OG_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"')
_URL_RE = re.compile(
    r'https?://music\.apple\.com/[a-z]{2}/(playlist|album|song)/[^\s"\']+', re.I
)
_PLID_RE = re.compile(r'pl\.[A-Za-z0-9]+')
_NUM_ID_RE = re.compile(r'/(?:album|song)/[^/]+/(\d+)')
_ISO_DUR_RE = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')


class AppleReadError(Exception):
    """The playlist could not be read. Message is safe to show the user."""


def parse_link(url_or: str) -> tuple[str, str, str | None]:
    """Classify an Apple Music link → ``(kind, url, song_id)``.

    ``kind`` is ``playlist`` / ``album`` / ``song``. An album link with
    ``?i=<songId>`` (how Apple shares a single song) comes back as kind
    ``song`` with the album URL to fetch and the song id to pick out."""
    s = (url_or or "").strip()
    m = _URL_RE.search(s)
    if not m:
        raise AppleReadError(
            "That doesn't look like an Apple Music playlist, album, or song link. "
            "Paste something like https://music.apple.com/us/playlist/name/pl.xxxxxxxx"
        )
    url, kind = m.group(0), m.group(1).lower()
    if kind == "playlist":
        if not _PLID_RE.search(url):
            raise AppleReadError(
                "That playlist link is missing its id (the pl.xxxxxxxx part)."
            )
        return "playlist", url, None
    if not _NUM_ID_RE.search(url):
        raise AppleReadError(f"That {kind} link is missing its numeric id.")
    if kind == "album":
        song_id = (parse_qs(urlparse(url).query).get("i") or [None])[0]
        if song_id:  # a single song shared via its album page
            return "song", url, song_id
        return "album", url, None
    return "song", url, None  # /song/ page


def valid_url(url_or: str) -> str:
    """Return the Apple Music URL to fetch, or raise (kept for compatibility)."""
    return parse_link(url_or)[1]


def link_key(url_or: str) -> str:
    """A stable id for the link — same source, same key, however it's pasted
    (country prefix and slug vary). Used to remember source → pushed playlist."""
    kind, url, song_id = parse_link(url_or)
    if kind == "playlist":
        return _PLID_RE.search(url).group(0)
    if song_id:
        return song_id
    return _NUM_ID_RE.search(url).group(1)


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


def read_link(url_or: str, *, timeout: int = 20):
    """Read any public Apple Music playlist / album / song link.

    Returns ``(name, [Track, ...], truncated)``."""
    kind, page, song_id = parse_link(url_or)
    req = urllib.request.Request(page, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise AppleReadError(
                f"That {kind} wasn't found. Check the link and make sure it's public."
            ) from e
        raise AppleReadError(f"Apple Music returned an error ({e.code}).") from e
    except Exception as e:
        raise AppleReadError(f"Couldn't reach Apple Music ({e}).") from e

    if kind == "song" and song_id is None:
        return parse_song_page(html)

    name, tracks, truncated = parse_page(html, song_id=song_id)
    if not tracks:
        if song_id:  # album page fetched for one song, row not found
            raise AppleReadError("Couldn't find that song on its album page.")
        raise AppleReadError(
            f"No tracks found (the {kind} may be private, empty, or in a format "
            "we can't read — try the CSV path with TuneMyMusic)."
        )
    if song_id:
        return tracks[0].title, tracks, False
    return name, tracks, truncated


def parse_page(html: str, *, song_id: str | None = None):
    """Pure parse of a fetched Apple playlist/album page — split out so it's
    testable. With ``song_id``, keep only that row (a song shared via ``?i=``);
    row ids look like ``track-lockup - <containerId> - <songId>``."""
    m = _SSD_RE.search(html)
    if not m:
        raise AppleReadError(
            "Apple didn't return track data (the link may be private, or the "
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
        if song_id and str(it.get("id", "")).split(" - ")[-1].strip() != song_id:
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


def _iso_duration_ms(s: str) -> int | None:
    """``PT3M32S`` → 212000. Returns None when the string doesn't parse."""
    m = _ISO_DUR_RE.fullmatch((s or "").strip())
    if not m or not any(m.groups()):
        return None
    h, mins, secs = (int(g) if g else 0 for g in m.groups())
    return ((h * 60 + mins) * 60 + secs) * 1000


def parse_song_page(html: str):
    """Pure parse of a fetched Apple /song/ page → ``(name, [Track], False)``.

    Song pages have no track-lockup rows; the track lives in the JSON-LD
    ``MusicComposition.audio`` (a MusicRecording) node instead."""
    rec = None
    for m in _LD_RE.finditer(html):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        cand = j.get("audio") if isinstance(j, dict) else None
        if isinstance(cand, dict) and cand.get("@type") == "MusicRecording":
            rec = cand
            break
        if isinstance(j, dict) and j.get("@type") == "MusicRecording":
            rec = j
            break
    if not rec or not rec.get("name"):
        raise AppleReadError(
            "Couldn't read that song from Apple's page (it may be private, or "
            "the page format changed)."
        )
    artist = ", ".join(
        a.get("name", "") for a in (rec.get("byArtist") or []) if a.get("name")
    )
    track = Track(
        title=str(rec["name"]).strip(),
        artist=artist,
        duration_ms=_iso_duration_ms(rec.get("duration", "")),
    )
    return track.title, [track], False
