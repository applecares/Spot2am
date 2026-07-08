"""Read a public Spotify playlist with no authentication.

Technique: Spotify's public embed widget (open.spotify.com/embed/playlist/<id>)
ships the track list inside a ``__NEXT_DATA__`` JSON blob — the same data the
browser widget renders. No login, no API key, no account touched.

Known limit: the embed page returns at most 100 tracks. For longer playlists we
surface a warning and the caller falls back to the manual (TuneMyMusic) path.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

EMBED_URL = "https://open.spotify.com/embed/playlist/{pid}"
EMBED_CAP = 100  # Spotify's embed page returns at most this many tracks.

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_PLAYLIST_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")
_BARE_ID_RE = re.compile(r"[A-Za-z0-9]{22}")
_NBSP = " "


@dataclass
class Track:
    title: str
    artist: str
    duration_ms: int | None = None
    explicit: bool = False
    spotify_uri: str | None = None


class SpotifyReadError(Exception):
    """The playlist could not be read. Message is safe to show the user."""


def parse_playlist_id(url_or_id: str) -> str:
    """Pull the playlist id out of any Spotify link, URI, or bare id."""
    s = (url_or_id or "").strip()
    if not s:
        raise SpotifyReadError("Paste a Spotify playlist link first.")
    m = _PLAYLIST_ID_RE.search(s)
    if m:
        return m.group(1)
    if _BARE_ID_RE.fullmatch(s):
        return s
    raise SpotifyReadError(
        "That doesn't look like a Spotify playlist link. Paste something like "
        "https://open.spotify.com/playlist/XXXXXXXXXXXXXXXXXXXXXX"
    )


def _clean_artist(subtitle: str) -> str:
    # The embed separates multiple artists with non-breaking spaces (U+00A0).
    return (subtitle or "").replace(_NBSP, " ").strip()


def _extract(html: str) -> tuple[str, list[dict]]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise SpotifyReadError(
            "Spotify didn't return track data. The playlist may be private, or "
            "the page format changed — try the manual fallback below."
        )
    try:
        data = json.loads(m.group(1))
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
        raw = entity["trackList"]
    except (KeyError, ValueError) as e:
        raise SpotifyReadError(
            "Couldn't find tracks in Spotify's response (playlist may be "
            "private or empty)."
        ) from e
    name = entity.get("name") or entity.get("title") or "Spotify Playlist"
    return name, raw


def read_playlist(url_or_id: str, *, timeout: int = 20):
    """Return ``(playlist_name, [Track, ...], truncated: bool)``.

    ``truncated`` is True when the playlist hit the 100-track embed cap and may
    have more tracks than we could read.
    """
    pid = parse_playlist_id(url_or_id)
    req = urllib.request.Request(EMBED_URL.format(pid=pid), headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SpotifyReadError(
                "That playlist wasn't found. Check the link, and make sure the "
                "playlist is public."
            ) from e
        raise SpotifyReadError(f"Spotify returned an error ({e.code}).") from e
    except Exception as e:  # network / DNS / timeout
        raise SpotifyReadError(f"Couldn't reach Spotify ({e}).") from e

    name, raw = _extract(html)
    tracks: list[Track] = []
    for t in raw:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        tracks.append(
            Track(
                title=title,
                artist=_clean_artist(t.get("subtitle")),
                duration_ms=t.get("duration"),
                explicit=bool(t.get("isExplicit")),
                spotify_uri=t.get("uri"),
            )
        )
    if not tracks:
        raise SpotifyReadError("The playlist appears to be empty.")
    truncated = len(tracks) >= EMBED_CAP
    return name, tracks, truncated
