"""Read a public Spotify playlist, album, or track with no authentication.

Technique: Spotify's public embed widget (open.spotify.com/embed/<kind>/<id>)
ships the track list inside a ``__NEXT_DATA__`` JSON blob — the same data the
browser widget renders. No login, no API key, no account touched. Albums use
the exact same shape as playlists; a track embed carries the track itself as
the entity.

Known limit: the embed page returns at most 100 tracks. For longer playlists we
surface a warning and the caller falls back to the token read (spotify_write)
or the manual (TuneMyMusic) path.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

EMBED_URL = "https://open.spotify.com/embed/{kind}/{sid}"
EMBED_CAP = 100  # Spotify's embed page returns at most this many tracks.

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_PLAYLIST_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")
_LINK_RE = re.compile(r"\b(playlist|album|track)[/:]([A-Za-z0-9]+)")
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
    kind, sid = parse_link(url_or_id)
    if kind != "playlist":
        raise SpotifyReadError("That's not a playlist link.")
    return sid


def parse_link(url_or_id: str) -> tuple[str, str]:
    """Classify any Spotify link, URI, or bare id → ``(kind, id)``.

    ``kind`` is one of ``playlist`` / ``album`` / ``track``; a bare 22-char id
    is assumed to be a playlist (the tool's main case)."""
    s = (url_or_id or "").strip()
    if not s:
        raise SpotifyReadError("Paste a Spotify link first.")
    m = _LINK_RE.search(s)
    if m:
        return m.group(1), m.group(2)
    if _BARE_ID_RE.fullmatch(s):
        return "playlist", s
    raise SpotifyReadError(
        "That doesn't look like a Spotify playlist, album, or track link. Paste "
        "something like https://open.spotify.com/playlist/XXXXXXXXXXXXXXXXXXXXXX"
    )


def _clean_artist(subtitle: str) -> str:
    # The embed separates multiple artists with non-breaking spaces (U+00A0).
    return (subtitle or "").replace(_NBSP, " ").strip()


def _entity(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise SpotifyReadError(
            "Spotify didn't return track data. The link may be private, or "
            "the page format changed — try the manual fallback below."
        )
    try:
        data = json.loads(m.group(1))
        return data["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, ValueError) as e:
        raise SpotifyReadError(
            "Couldn't find tracks in Spotify's response (the link may be "
            "private or empty)."
        ) from e


def parse_embed(html: str, kind: str = "playlist"):
    """Pure parse of a fetched embed page → ``(name, [Track, ...], truncated)``.

    Split out from the fetch so it's testable offline."""
    entity = _entity(html)
    name = entity.get("name") or entity.get("title") or "Spotify Playlist"

    if kind == "track":
        # The entity IS the track; artists come as a list, not a subtitle.
        artist = ", ".join(
            a.get("name", "") for a in (entity.get("artists") or []) if a.get("name")
        )
        title = (entity.get("title") or entity.get("name") or "").strip()
        if not title:
            raise SpotifyReadError("Couldn't read that track from Spotify.")
        track = Track(
            title=title,
            artist=artist,
            duration_ms=entity.get("duration"),
            explicit=bool(entity.get("isExplicit")),
            spotify_uri=entity.get("uri"),
        )
        return name, [track], False

    raw = entity.get("trackList")
    if raw is None:
        raise SpotifyReadError(
            "Couldn't find tracks in Spotify's response (the link may be "
            "private or empty)."
        )
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
        raise SpotifyReadError(f"The {kind} appears to be empty.")
    truncated = len(tracks) >= EMBED_CAP
    return name, tracks, truncated


def read_link(url_or_id: str, *, timeout: int = 20):
    """Read any public Spotify playlist / album / track link with no login.

    Returns ``(name, [Track, ...], truncated: bool)``. ``truncated`` is True
    when the list hit the 100-track embed cap and may have more tracks than we
    could read.
    """
    kind, sid = parse_link(url_or_id)
    req = urllib.request.Request(
        EMBED_URL.format(kind=kind, sid=sid), headers={"User-Agent": _UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SpotifyReadError(
                f"That {kind} wasn't found. Check the link, and make sure it's public."
            ) from e
        raise SpotifyReadError(f"Spotify returned an error ({e.code}).") from e
    except Exception as e:  # network / DNS / timeout
        raise SpotifyReadError(f"Couldn't reach Spotify ({e}).") from e

    return parse_embed(html, kind)
